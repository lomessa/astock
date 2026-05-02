"""
简易回测引擎
============
针对涨停策略的简化回测：
- 买入条件：当日收盘前涨停（视为买入成功）
- 卖出条件：次日开盘价卖出（测算溢价）
- 不支持滑点、手续费默认千分之一

注意：打板策略的真实回测需要 tick 数据，本引擎仅做粗略验证。
"""
from typing import List, Dict, Any, Optional
from datetime import datetime

import pandas as pd
import numpy as np

from astock.data import DataClient


class SimpleBacktest:
    """简化回测：验证昨日涨停选股，今日溢价情况"""

    def __init__(self, initial_cash: float = 100000.0):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.trades: List[Dict[str, Any]] = []

    def run_premium_test(self, data: Any, trade_dates: List[str]) -> pd.DataFrame:
        """
        测试「昨日涨停股」在次日的平均溢价与胜率

        Parameters
        ----------
        trade_dates : list of str
            回测日期列表（YYYYMMDD）

        Returns
        -------
        pd.DataFrame
            每日统计
        """
        records = []
        for date in trade_dates:
            try:
                df_prev = data.get_zt_pool_previous(date)
                if df_prev.empty or "change_pct" not in df_prev.columns:
                    continue
                avg_premium = df_prev["change_pct"].mean()
                win_rate = (df_prev["change_pct"] > 0).sum() / len(df_prev)
                records.append({
                    "date": date,
                    "count": len(df_prev),
                    "avg_premium": round(avg_premium, 2),
                    "win_rate": round(win_rate, 3),
                    "max_gain": round(df_prev["change_pct"].max(), 2),
                    "max_loss": round(df_prev["change_pct"].min(), 2),
                })
            except Exception as e:
                print(f"[WARN] {date} 回测异常: {e}")
        return pd.DataFrame(records)

    def summary(self, df: pd.DataFrame) -> Dict[str, Any]:
        """回测结果汇总"""
        if df.empty:
            return {}
        return {
            "total_days": len(df),
            "avg_premium": round(df["avg_premium"].mean(), 2),
            "avg_win_rate": round(df["win_rate"].mean(), 3),
            "positive_days": (df["avg_premium"] > 0).sum(),
            "negative_days": (df["avg_premium"] <= 0).sum(),
        }
