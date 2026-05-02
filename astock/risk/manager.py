"""风控管理器"""
from typing import List, Optional

import pandas as pd
import numpy as np

from astock.config import get_settings


class RiskManager:
    """风控与仓位管理"""

    def __init__(self):
        self.cfg = get_settings().risk

    def filter_candidates(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        对候选股票进行风控过滤
        """
        if df.empty:
            return df

        # 黑名单过滤
        if self.cfg.blacklist:
            df = df[~df["symbol"].isin(self.cfg.blacklist)]

        # 市值过滤（亿元）
        if "float_mv" in df.columns:
            df = df[(df["float_mv"] >= self.cfg.min_market_cap) & (df["float_mv"] <= self.cfg.max_market_cap)]

        # 价格过滤（排除过高或过低）
        if "close" in df.columns:
            df = df[(df["close"] >= 3.0) & (df["close"] <= 200.0)]

        # 排除异常值（如 confidence 为 NaN）
        if "confidence" in df.columns:
            df = df[df["confidence"].notna()]

        return df.reset_index(drop=True)

    def calculate_position(self, confidence: float, available_cash: float, total_asset: float) -> float:
        """
        计算建议买入金额

        Parameters
        ----------
        confidence : float
            信号置信度
        available_cash : float
            可用现金
        total_asset : float
            总资产

        Returns
        -------
        float
            建议买入金额
        """
        base_ratio = self.cfg.max_position_per_stock
        if confidence >= 0.85:
            ratio = base_ratio
        elif confidence >= 0.70:
            ratio = base_ratio * 0.7
        elif confidence >= 0.55:
            ratio = base_ratio * 0.4
        else:
            ratio = base_ratio * 0.2

        max_amount = total_asset * ratio
        return min(max_amount, available_cash)

    def should_stop_loss(self, entry_price: float, current_price: float) -> bool:
        """判断是否触发止损"""
        return (current_price - entry_price) / entry_price <= -self.cfg.stop_loss_ratio

    def should_stop_profit(self, entry_price: float, current_price: float) -> bool:
        """判断是否触发止盈"""
        return (current_price - entry_price) / entry_price >= self.cfg.stop_profit_ratio

    def check_daily_loss(self, daily_pnl: float, total_asset: float) -> bool:
        """检查当日回撤是否超限"""
        return daily_pnl / total_asset <= -self.cfg.max_daily_loss_ratio
