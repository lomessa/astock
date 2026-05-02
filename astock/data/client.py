"""
聚合数据客户端
==============
多源聚合 + 本地持久化 + 自动降级。
对上层保持与 AKShareData 完全一致的接口。
"""
import warnings
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Type

import pandas as pd

from astock.data.sources.base import DataSource
from astock.data.store import LocalStore
from astock.config import get_settings

# 延迟导入具体源，避免未安装依赖时报错
_SOURCE_MAP: Dict[str, Type[DataSource]] = {}


def _register_sources():
    global _SOURCE_MAP
    if _SOURCE_MAP:
        return
    try:
        from astock.data.sources.akshare_src import AKShareSource
        _SOURCE_MAP["akshare"] = AKShareSource
    except Exception:
        pass
    try:
        from astock.data.sources.baostock_src import BaostockSource
        _SOURCE_MAP["baostock"] = BaostockSource
    except Exception:
        pass
    try:
        from astock.data.sources.tushare_src import TushareSource
        _SOURCE_MAP["tushare"] = TushareSource
    except Exception:
        pass
    try:
        from astock.data.sources.yahoo_src import YahooSource
        _SOURCE_MAP["yahoo"] = YahooSource
    except Exception:
        pass


class DataClient(DataSource):
    """
    聚合数据客户端
    ==============
    1. 优先读取本地 SQLite
    2. 按配置优先级遍历外部源
    3. 外部源成功时自动写回本地
    4. 全部失败返回空 DataFrame（与 AKShareData 行为一致）
    """

    name = "client"

    # 方法 -> 源优先级（仅外部源，LocalStore 默认最优先）
    _METHOD_PRIORITY: Dict[str, List[str]] = {
        "get_daily":       ["tushare", "baostock", "yahoo", "akshare"],
        "get_index_daily": ["tushare", "baostock", "yahoo", "akshare"],
        "get_stock_info":  ["tushare", "akshare"],
        "get_zt_pool":           ["akshare"],
        "get_zt_pool_previous":  ["akshare"],
        "get_zt_pool_strong":    ["akshare"],
        "get_zt_pool_sub_new":   ["akshare"],
        "get_zt_pool_zbgc":      ["akshare"],
        "get_sector_hot":        ["akshare"],
        "get_sector_cons":       ["akshare"],
        "get_longhubang":        ["akshare"],
        "get_limit_up_statistics": ["akshare"],
    }

    def __init__(self):
        _register_sources()
        self.cfg = get_settings().data
        self.store = LocalStore(self.cfg.db_path)
        self._sources: Dict[str, DataSource] = {}
        self._init_sources()

    def _init_sources(self):
        """根据配置初始化启用的数据源"""
        enabled = []
        for name in self.cfg.source_priority:
            if name == "akshare" and getattr(self.cfg, "enable_akshare", True):
                enabled.append(name)
            elif name == "baostock" and getattr(self.cfg, "enable_baostock", True):
                enabled.append(name)
            elif name == "tushare" and getattr(self.cfg, "enable_tushare", False):
                enabled.append(name)
            elif name == "yahoo" and getattr(self.cfg, "enable_yahoo", True):
                enabled.append(name)

        for name in enabled:
            cls = _SOURCE_MAP.get(name)
            if cls is None:
                continue
            try:
                self._sources[name] = cls()
                print(f"[DataClient] 数据源已加载: {name}")
            except Exception as e:
                print(f"[DataClient] 数据源加载失败 {name}: {e}")

    # ------------------------------------------------------------------
    # 通用 fallback 调用器
    # ------------------------------------------------------------------
    def _fetch_with_fallback(self, method: str, *args, **kwargs) -> pd.DataFrame:
        """
        调用逻辑：
        1. 尝试 LocalStore（如果该方法支持本地缓存）
        2. 按优先级遍历外部源
        3. 任一源成功 -> 写回 LocalStore -> 返回
        4. 全部失败 -> 返回空 DataFrame
        """
        # 1. 尝试本地
        local_df = self._try_local(method, *args, **kwargs)
        if local_df is not None:
            return local_df

        # 2. 遍历外部源
        priority = self._METHOD_PRIORITY.get(method, [])
        last_err = None
        for src_name in priority:
            src = self._sources.get(src_name)
            if src is None:
                continue
            try:
                df = getattr(src, method)(*args, **kwargs)
                if isinstance(df, pd.DataFrame) and not df.empty:
                    # 写回本地
                    self._save_to_local(method, df, *args, **kwargs)
                    return df
            except Exception as e:
                last_err = e
                print(f"[DataClient] {method} @ {src_name} 失败: {e}")
                continue

        # 3. 全部失败
        if last_err:
            print(f"[WARN] DataClient {method} 全部源失败，返回空 DataFrame。最后错误: {last_err}")
        return pd.DataFrame()

    def _try_local(self, method: str, *args, **kwargs) -> Optional[pd.DataFrame]:
        """尝试从本地读取。如果数据不存在或过期，返回 None。"""
        try:
            if method == "get_daily":
                symbol = args[0] if args else kwargs.get("symbol")
                start = kwargs.get("start_date") if "start_date" in kwargs else (args[1] if len(args) > 1 else None)
                end = kwargs.get("end_date") if "end_date" in kwargs else (args[2] if len(args) > 2 else None)
                if start is None:
                    start = (datetime.now() - timedelta(days=self.cfg.history_days)).strftime("%Y%m%d")
                if end is None:
                    end = datetime.now().strftime("%Y%m%d")
                # 标准化为 YYYY-MM-DD
                start_iso = pd.to_datetime(start).strftime("%Y-%m-%d")
                end_iso = pd.to_datetime(end).strftime("%Y-%m-%d")
                if self.store.daily_coverage(symbol, start_iso, end_iso):
                    return self.store.load_daily(symbol, start_iso, end_iso)
                return None

            if method == "get_zt_pool":
                trade_date = kwargs.get("trade_date") or args[0] if args else datetime.now().strftime("%Y%m%d")
                df = self.store.load_zt_pool(trade_date)
                return df if not df.empty else None

            if method == "get_zt_pool_previous":
                trade_date = kwargs.get("trade_date") or args[0] if args else datetime.now().strftime("%Y%m%d")
                df = self.store.load_zt_pool_prev(trade_date)
                return df if not df.empty else None

            if method == "get_stock_info":
                df = self.store.load_stock_info(max_age_minutes=30)
                return df if not df.empty else None

            if method == "get_index_daily":
                symbol = args[0] if args else kwargs.get("symbol", "000001")
                start = (datetime.now() - timedelta(days=365 * 2)).strftime("%Y%m%d")
                end = datetime.now().strftime("%Y%m%d")
                start_iso = pd.to_datetime(start).strftime("%Y-%m-%d")
                end_iso = pd.to_datetime(end).strftime("%Y-%m-%d")
                if self.store.index_daily_coverage(symbol, start_iso, end_iso):
                    return self.store.load_index_daily(symbol, start_iso, end_iso)
                return None

        except Exception as e:
            print(f"[DataClient] 本地读取 {method} 异常: {e}")
        return None

    def _save_to_local(self, method: str, df: pd.DataFrame, *args, **kwargs):
        """将外部源返回的数据写回本地"""
        try:
            if method == "get_daily":
                symbol = args[0] if args else kwargs.get("symbol")
                if symbol and "symbol" not in df.columns:
                    df = df.copy()
                    df["symbol"] = str(symbol).zfill(6)
                # 标准化日期列
                if "date" in df.columns:
                    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
                self.store.save_daily(df)

            elif method == "get_zt_pool":
                if "trade_date" in df.columns:
                    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
                self.store.save_zt_pool(df)

            elif method == "get_zt_pool_previous":
                if "trade_date" in df.columns:
                    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
                self.store.save_zt_pool_prev(df)

            elif method == "get_stock_info":
                self.store.save_stock_info(df)

            elif method == "get_index_daily":
                symbol = args[0] if args else kwargs.get("symbol", "000001")
                if "symbol" not in df.columns:
                    df = df.copy()
                    df["symbol"] = str(symbol).zfill(6)
                if "date" in df.columns:
                    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
                self.store.save_index_daily(df)

        except Exception as e:
            print(f"[DataClient] 本地保存 {method} 异常: {e}")

    # ------------------------------------------------------------------
    # 对外接口（委托给 fallback）
    # ------------------------------------------------------------------
    def get_daily(self, symbol: str, start_date: Optional[str] = None,
                  end_date: Optional[str] = None) -> pd.DataFrame:
        return self._fetch_with_fallback("get_daily", symbol, start_date=start_date, end_date=end_date)

    def get_zt_pool(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        return self._fetch_with_fallback("get_zt_pool", trade_date)

    def get_zt_pool_previous(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        return self._fetch_with_fallback("get_zt_pool_previous", trade_date)

    def get_zt_pool_strong(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        return self._fetch_with_fallback("get_zt_pool_strong", trade_date)

    def get_zt_pool_sub_new(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        return self._fetch_with_fallback("get_zt_pool_sub_new", trade_date)

    def get_zt_pool_zbgc(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        return self._fetch_with_fallback("get_zt_pool_zbgc", trade_date)

    def get_stock_info(self) -> pd.DataFrame:
        return self._fetch_with_fallback("get_stock_info")

    def get_sector_hot(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        """
        获取板块资金流向排名。
        成功获取后自动写入本地 sector_rank 表，便于策略判断板块持续性。
        """
        df = self._fetch_with_fallback("get_sector_hot")
        if not df.empty and "sector_name" in df.columns:
            # 自动保存到本地（用 trade_date 或今天）
            save_date = trade_date or datetime.now().strftime("%Y%m%d")
            try:
                self.store.save_sector_rank(df, save_date)
            except Exception as e:
                print(f"[DataClient] 保存板块排名到本地失败: {e}")
        return df

    def get_sector_hot_history(self, days: int = 5) -> pd.DataFrame:
        """读取最近N天的板块排名历史（本地）"""
        return self.store.load_sector_rank_history(days=days)

    def get_sector_cons(self, sector_name: str) -> pd.DataFrame:
        """
        获取板块成分股。
        由于板块成分股数量大、变化快，不做本地持久化，直接透传 AKShare。
        """
        src = self._sources.get("akshare")
        if src is not None:
            try:
                return src.get_sector_cons(sector_name)
            except Exception as e:
                print(f"[DataClient] get_sector_cons @ akshare 失败: {e}")
        return pd.DataFrame()

    def get_longhubang(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        return self._fetch_with_fallback("get_longhubang", trade_date)

    def get_limit_up_statistics(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        return self._fetch_with_fallback("get_limit_up_statistics", trade_date)

    def get_index_daily(self, symbol: str = "000001") -> pd.DataFrame:
        return self._fetch_with_fallback("get_index_daily", symbol)

    def clear_cache(self):
        for src in self._sources.values():
            try:
                src.clear_cache()
            except Exception:
                pass
        # 也清空本地 store 的表（可选）
        try:
            import sqlite3
            with sqlite3.connect(self.store.db_path) as conn:
                for table in ["daily", "zt_pool", "zt_pool_prev", "stock_info", "index_daily"]:
                    conn.execute(f"DELETE FROM {table}")
                conn.commit()
        except Exception:
            pass
