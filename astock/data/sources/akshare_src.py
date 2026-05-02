"""
AKShare 数据源实现
==================
从原 AKShareData 拆分出的纯 AKShare 源，保留全部功能。
"""
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd
import akshare as ak
from tenacity import retry, stop_after_attempt, wait_fixed

from astock.data.sources.base import DataSource
from astock.config import get_settings
from astock.data.cache import DataCache


class AKShareSource(DataSource):
    """AKShare 数据源"""

    name = "akshare"

    def __init__(self):
        self.cfg = get_settings().data
        self.cache = DataCache(self.cfg.cache_dir, ttl=self.cfg.cache_ttl)

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def get_daily(self, symbol: str, start_date: Optional[str] = None,
                  end_date: Optional[str] = None) -> pd.DataFrame:
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=self.cfg.history_days)).strftime("%Y%m%d")
        cached = self.cache.get("daily", symbol=symbol, start=start_date, end=end_date)
        if cached is not None:
            return cached
        try:
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_date,
                                    end_date=end_date, adjust="qfq")
        except Exception as e:
            print(f"[WARN] AKShare get_daily {symbol} failed: {e}")
            return pd.DataFrame()
        if df.empty:
            return df
        df = df.rename(columns={
            "日期": "date", "股票代码": "symbol",
            "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "振幅": "amplitude",
            "涨跌幅": "change_pct", "涨跌额": "change", "换手率": "turnover",
        })
        if "symbol" in df.columns:
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        self.cache.set("daily", df, symbol=symbol, start=start_date, end=end_date)
        return df

    def get_last_price(self, symbol: str) -> float:
        df = self.get_daily(symbol)
        if df.empty:
            return 0.0
        return float(df["close"].iloc[-1])

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def get_zt_pool(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")
        cached = self.cache.get("zt_pool", date=trade_date)
        if cached is not None:
            return cached

        # 1. 先尝试东方财富
        try:
            df = ak.stock_zt_pool_em(date=trade_date)
            rename_map = {
                "代码": "symbol", "名称": "name", "涨跌幅": "change_pct",
                "最新价": "close", "成交额": "amount", "流通市值": "float_mv",
                "总市值": "total_mv", "换手率": "turnover", "封板资金": "seal_amount",
                "首次封板时间": "first_seal_time", "最后封板时间": "last_seal_time",
                "炸板次数": "open_times", "涨停统计": "zt_stats",
                "连板数": "board_num", "所属行业": "industry",
            }
            df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
            for col in ["change_pct", "close", "amount", "float_mv", "total_mv", "seal_amount", "board_num", "open_times"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            # 单位统一为「亿」
            for col in ["amount", "float_mv", "total_mv", "seal_amount"]:
                if col in df.columns:
                    df[col] = df[col] / 1e8
            if "symbol" in df.columns:
                df["symbol"] = df["symbol"].astype(str).str.zfill(6)
            df["trade_date"] = pd.to_datetime(trade_date)
            self.cache.set("zt_pool", df, date=trade_date)
            return df
        except Exception as e:
            print(f"[WARN] AKShare 东方财富涨停池失败: {e}")

        # 2. 降级：如果是今日，用新浪财经即时行情筛选涨停股
        today = datetime.now().strftime("%Y%m%d")
        if trade_date != today:
            return pd.DataFrame()

        print("[INFO] 尝试新浪即时行情降级获取今日涨停池...")
        try:
            df = ak.stock_zh_a_spot()
            # 涨停判断：主板/ST >= 9.8%，创业板/科创板 >= 19.8%
            # 新浪接口没有市场标识，统一用 >= 9.8 筛选（会包含部分创业板，但不会漏掉主板）
            df = df[df["涨跌幅"] >= 9.8].copy()
            if df.empty:
                return pd.DataFrame()
            rename_map = {
                "代码": "symbol", "名称": "name", "涨跌幅": "change_pct",
                "最新价": "close", "成交额": "amount",
                "成交量": "volume", "昨收": "pre_close",
            }
            df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
            for col in ["change_pct", "close", "amount", "volume", "pre_close"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            # 新浪成交额单位是元，统一为亿
            for col in ["amount"]:
                if col in df.columns:
                    df[col] = df[col] / 1e8
            if "symbol" in df.columns:
                df["symbol"] = df["symbol"].astype(str).str.zfill(6)
            # 新浪缺少的字段设默认值，避免策略崩溃
            df["board_num"] = 1      # 默认首板（策略会结合昨日数据再过滤）
            df["open_times"] = 0
            df["seal_amount"] = df.get("amount", 0) * 0.1  # 粗略估计封单为成交额的10%
            df["float_mv"] = pd.NA
            df["turnover"] = pd.NA
            df["industry"] = ""
            df["first_seal_time"] = ""
            df["last_seal_time"] = ""
            df["trade_date"] = pd.to_datetime(trade_date)
            self.cache.set("zt_pool", df, date=trade_date)
            return df
        except Exception as e2:
            print(f"[WARN] AKShare 新浪涨停池降级也失败: {e2}")
            return pd.DataFrame()

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def get_zt_pool_previous(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")
        cached = self.cache.get("zt_pool_prev", date=trade_date)
        if cached is not None:
            return cached

        # 1. 先尝试东方财富
        try:
            df = ak.stock_zt_pool_previous_em(date=trade_date)
            rename_map = {
                "代码": "symbol", "名称": "name", "涨跌幅": "change_pct",
                "最新价": "close", "成交额": "amount",
                "流通市值": "float_mv", "换手率": "turnover",
                "涨停统计": "zt_stats", "所属行业": "industry",
            }
            df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
            for col in ["change_pct", "close", "amount", "float_mv", "total_mv", "turnover"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            # 单位统一为「亿」
            for col in ["amount", "float_mv", "total_mv"]:
                if col in df.columns:
                    df[col] = df[col] / 1e8
            if "symbol" in df.columns:
                df["symbol"] = df["symbol"].astype(str).str.zfill(6)
            df["trade_date"] = pd.to_datetime(trade_date)
            self.cache.set("zt_pool_prev", df, date=trade_date)
            return df
        except Exception as e:
            print(f"[WARN] AKShare 东方财富昨日涨停表现失败: {e}")

        # 2. 降级：如果是今日，用新浪即时行情近似
        today = datetime.now().strftime("%Y%m%d")
        if trade_date != today:
            return pd.DataFrame()

        print("[INFO] 尝试新浪即时行情降级获取昨日涨停今日表现...")
        try:
            # 需要昨日涨停列表 + 今日行情
            yest = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
            # 尝试从缓存获取昨日涨停
            yest_zt = self.cache.get("zt_pool", date=yest)
            if yest_zt is None or yest_zt.empty:
                print(f"[WARN] 缓存中无昨日({yest})涨停数据，无法计算昨日涨停表现")
                return pd.DataFrame()

            # 获取今日行情
            today_spot = ak.stock_zh_a_spot()
            today_spot = today_spot.rename(columns={"代码": "symbol", "名称": "name", "涨跌幅": "change_pct", "最新价": "close", "成交额": "amount", "流通市值": "float_mv", "换手率": "turnover"})
            for col in ["change_pct", "close", "amount", "float_mv", "turnover"]:
                if col in today_spot.columns:
                    today_spot[col] = pd.to_numeric(today_spot[col], errors="coerce")
            for col in ["amount", "float_mv"]:
                if col in today_spot.columns:
                    today_spot[col] = today_spot[col] / 1e8
            today_spot["symbol"] = today_spot["symbol"].astype(str).str.zfill(6)

            # 合并：昨日涨停股票在今日的表现
            symbols = yest_zt["symbol"].astype(str).str.zfill(6).tolist()
            df = today_spot[today_spot["symbol"].isin(symbols)].copy()
            if df.empty:
                return pd.DataFrame()
            df["trade_date"] = pd.to_datetime(trade_date)
            self.cache.set("zt_pool_prev", df, date=trade_date)
            return df
        except Exception as e2:
            print(f"[WARN] AKShare 新浪昨日涨停表现降级也失败: {e2}")
            return pd.DataFrame()

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def get_zt_pool_strong(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")
        cached = self.cache.get("zt_pool_strong", date=trade_date)
        if cached is not None:
            return cached
        df = ak.stock_zt_pool_strong_em(date=trade_date)
        if "代码" in df.columns:
            df = df.rename(columns={"代码": "symbol", "名称": "name"})
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        df["trade_date"] = pd.to_datetime(trade_date)
        self.cache.set("zt_pool_strong", df, date=trade_date)
        return df

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def get_zt_pool_sub_new(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")
        cached = self.cache.get("zt_pool_subnew", date=trade_date)
        if cached is not None:
            return cached
        df = ak.stock_zt_pool_sub_new_em(date=trade_date)
        if "代码" in df.columns:
            df = df.rename(columns={"代码": "symbol", "名称": "name"})
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        df["trade_date"] = pd.to_datetime(trade_date)
        self.cache.set("zt_pool_subnew", df, date=trade_date)
        return df

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def get_zt_pool_zbgc(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")
        cached = self.cache.get("zt_pool_zbgc", date=trade_date)
        if cached is not None:
            return cached
        df = ak.stock_zt_pool_zbgc_em(date=trade_date)
        rename_map = {
            "代码": "symbol", "名称": "name", "涨跌幅": "change_pct",
            "最新价": "close", "涨停价": "limit_price", "成交额": "amount",
            "流通市值": "float_mv", "总市值": "total_mv", "换手率": "turnover",
            "涨速": "rise_speed", "首次封板时间": "first_seal_time",
            "炸板次数": "open_times", "涨停统计": "zt_stats",
            "振幅": "amplitude", "所属行业": "industry",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        if "symbol" in df.columns:
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        for col in ["change_pct", "close", "amount", "float_mv", "total_mv", "turnover", "open_times"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        # 单位统一为「亿」
        for col in ["amount", "float_mv", "total_mv"]:
            if col in df.columns:
                df[col] = df[col] / 1e8
        df["trade_date"] = pd.to_datetime(trade_date)
        self.cache.set("zt_pool_zbgc", df, date=trade_date)
        return df

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def get_stock_info(self) -> pd.DataFrame:
        cached = self.cache.get("stock_info")
        if cached is not None:
            return cached

        # 1. 先尝试东方财富
        try:
            df = ak.stock_zh_a_spot_em()
            rename_map = {
                "代码": "symbol", "名称": "name", "最新价": "price",
                "涨跌幅": "change_pct", "涨跌额": "change",
                "成交量": "volume", "成交额": "amount",
                "振幅": "amplitude", "最高": "high", "最低": "low",
                "今开": "open", "昨收": "pre_close",
                "量比": "volume_ratio", "换手率": "turnover",
                "市盈率-动态": "pe_ttm", "市净率": "pb",
                "总市值": "total_mv", "流通市值": "float_mv",
                "涨速": "rise_speed", "5分钟涨跌": "change_5min",
                "60日涨跌幅": "change_60d", "年初至今涨跌幅": "change_ytd",
            }
            df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
            for col in ["price", "change_pct", "volume", "amount", "turnover", "total_mv", "float_mv", "pe_ttm", "pb", "change_60d", "change_ytd"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            # 单位统一为「亿」
            for col in ["amount", "float_mv", "total_mv"]:
                if col in df.columns:
                    df[col] = df[col] / 1e8
            if "symbol" in df.columns:
                df["symbol"] = df["symbol"].astype(str).str.zfill(6)
            self.cache.set("stock_info", df)
            return df
        except Exception as e:
            print(f"[WARN] AKShare 东方财富 stock_info 失败: {e}，尝试新浪降级...")

        # 2. 降级到新浪财经即时行情
        df = ak.stock_zh_a_spot()
        rename_map = {
            "代码": "symbol", "名称": "name", "最新价": "price",
            "涨跌幅": "change_pct", "涨跌额": "change",
            "成交量": "volume", "成交额": "amount",
            "最高": "high", "最低": "low",
            "今开": "open", "昨收": "pre_close",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        for col in ["price", "change_pct", "volume", "amount", "high", "low", "open", "pre_close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        # 新浪成交额单位是元，统一为亿
        for col in ["amount"]:
            if col in df.columns:
                df[col] = df[col] / 1e8
        if "symbol" in df.columns:
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        self.cache.set("stock_info", df)
        return df

    def get_realtime_quotes(self, symbols: List[str]) -> pd.DataFrame:
        df = self.get_stock_info()
        return df[df["symbol"].isin(symbols)].copy()

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def get_sector_cons(self, sector_name: str) -> pd.DataFrame:
        """获取行业板块成分股（含最新价、涨跌幅、换手率）"""
        df = ak.stock_board_industry_cons_em(symbol=sector_name)
        rename_map = {
            "代码": "symbol", "名称": "name", "最新价": "price",
            "涨跌幅": "change_pct", "换手率": "turnover", "成交额": "amount",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        if "symbol" in df.columns:
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        for col in ["change_pct", "turnover", "price"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def get_sector_hot(self) -> pd.DataFrame:
        cached = self.cache.get("sector_hot")
        if cached is not None:
            return cached
        df = ak.stock_sector_fund_flow_rank(indicator="5日")
        # 标准化列名，便于策略和本地存储统一处理
        rename_map = {
            "名称": "sector_name",
            "序号": "sector_rank",
            "5日涨跌幅": "change_pct",
            "5日主力净流入-净额": "main_inflow",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        for col in ["sector_rank", "change_pct", "main_inflow"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        self.cache.set("sector_hot", df)
        return df

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def get_longhubang(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")
        cached = self.cache.get("lhb", date=trade_date)
        if cached is not None:
            return cached
        df = ak.stock_lhb_detail_daily_sina(start_date=trade_date, end_date=trade_date)
        self.cache.set("lhb", df, date=trade_date)
        return df

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def get_limit_up_statistics(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")
        cached = self.cache.get("zt_stats", date=trade_date)
        if cached is not None:
            return cached
        df = ak.stock_zt_pool_dtgc_em(date=trade_date)
        if "代码" in df.columns:
            df = df.rename(columns={"代码": "symbol", "名称": "name", "涨停原因": "reason"})
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        self.cache.set("zt_stats", df, date=trade_date)
        return df

    def get_index_daily(self, symbol: str = "000001") -> pd.DataFrame:
        cache_key = f"index_{symbol}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        df = ak.index_zh_a_hist(symbol=symbol, period="daily")
        df = df.rename(columns={
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
        })
        df["date"] = pd.to_datetime(df["date"])
        self.cache.set(cache_key, df)
        return df

    def clear_cache(self):
        self.cache.clear()
