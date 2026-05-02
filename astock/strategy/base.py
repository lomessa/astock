"""策略基类"""
from abc import ABC, abstractmethod
from typing import Dict, List, Any
from datetime import datetime

import pandas as pd


class StrategyBase(ABC):
    """所有短线策略的基类"""

    name: str = "base"

    @abstractmethod
    def select_candidates(self, data, trade_date: str) -> pd.DataFrame:
        """
        筛选候选标的

        Parameters
        ----------
        data : AKShareData
            数据客户端实例
        trade_date : str
            交易日期，格式 YYYYMMDD

        Returns
        -------
        pd.DataFrame
            必须包含列：symbol, name, score, reason
            可选：confidence（0-1）, suggested_position（建议仓位比例）
        """
        pass

    def pre_market_filter(self, data, trade_date: str) -> pd.DataFrame:
        """盘前过滤（默认调用 select_candidates）"""
        return self.select_candidates(data, trade_date)

    def intraday_alert(self, data, trade_date: str) -> pd.DataFrame:
        """盘中预警（默认返回空，子类可覆盖）"""
        return pd.DataFrame()

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name}
