"""
数据源抽象基类
===============
所有外部数据源的统一接口。
不支持的接口应抛出 NotImplementedError，由聚合层处理降级。
"""
from abc import ABC, abstractmethod
from typing import List, Optional
from datetime import datetime, timedelta

import pandas as pd


class DataSource(ABC):
    """数据源抽象基类"""

    name: str = "base"

    # ------------------------------------------------------------------
    # 基础行情（所有源应尽量实现）
    # ------------------------------------------------------------------
    @abstractmethod
    def get_daily(self, symbol: str, start_date: Optional[str] = None,
                  end_date: Optional[str] = None) -> pd.DataFrame:
        """
        个股历史日线（前复权），返回列标准化
        必须包含列: date, open, high, low, close, volume
        可选列: amount, amplitude, change_pct, change, turnover
        """
        raise NotImplementedError

    def get_last_price(self, symbol: str) -> float:
        """最新收盘价 — 默认基于 get_daily 计算，子类可覆盖以提高性能"""
        df = self.get_daily(symbol)
        if df.empty:
            return 0.0
        return float(df["close"].iloc[-1])

    # ------------------------------------------------------------------
    # 涨停数据（AKShare 独有，其他源通常不支持）
    # ------------------------------------------------------------------
    def get_zt_pool(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        """东方财富涨停股池"""
        raise NotImplementedError

    def get_zt_pool_previous(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        """昨日涨停股今日表现"""
        raise NotImplementedError

    def get_zt_pool_strong(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        """强势股池"""
        raise NotImplementedError

    def get_zt_pool_sub_new(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        """次新股涨停池"""
        raise NotImplementedError

    def get_zt_pool_zbgc(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        """炸板股池"""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 盘面辅助数据
    # ------------------------------------------------------------------
    def get_stock_info(self) -> pd.DataFrame:
        """A 股基础信息（名称、行业、市值等）"""
        raise NotImplementedError

    def get_realtime_quotes(self, symbols: List[str]) -> pd.DataFrame:
        """批量获取实时行情 — 默认基于 get_stock_info 过滤"""
        df = self.get_stock_info()
        return df[df["symbol"].isin(symbols)].copy()

    def get_sector_hot(self) -> pd.DataFrame:
        """板块资金流向"""
        raise NotImplementedError

    def get_sector_cons(self, sector_name: str) -> pd.DataFrame:
        """板块成分股"""
        raise NotImplementedError

    def get_longhubang(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        """龙虎榜数据"""
        raise NotImplementedError

    def get_limit_up_statistics(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        """涨停板复盘统计（包含涨停原因）"""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 指数与情绪
    # ------------------------------------------------------------------
    def get_index_daily(self, symbol: str = "000001") -> pd.DataFrame:
        """指数日线（默认上证指数）"""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def clear_cache(self):
        """清空本地缓存（如有）"""
        pass
