"""
GP 策略包装器
=============
将遗传规划发现的因子表达式封装为 AStock 策略。
"""
import pandas as pd
from deap import gp

from astock.strategy.base import StrategyBase
from .data_prep import prepare_data_from_db


class GPDipBuyStrategy(StrategyBase):
    """
    基于 GP 因子的次日逢低买入策略
    """

    name = "gp_dip_buy"

    def __init__(self, factor_expr=None, pset=None):
        self.factor_expr = factor_expr
        self.pset = pset

    def select_candidates(self, data, trade_date: str) -> pd.DataFrame:
        # 准备近 30 天数据计算因子
        data_dict, _ = prepare_data_from_db(trade_date, lookback_days=30)
        if data_dict is None:
            return pd.DataFrame()

        # 更新 pset context 为当前数据
        if self.pset is not None:
            for name, df in data_dict.items():
                self.pset.context[name] = df

        # pset arity=0 时 compile 直接返回结果
        factor = gp.compile(expr=self.factor_expr, pset=self.pset)

        # 取最新一天因子值
        if not isinstance(factor, pd.DataFrame) or factor.empty or len(factor) == 0:
            return pd.DataFrame()

        latest = factor.iloc[-1].dropna()
        if latest.empty:
            return pd.DataFrame()

        # 若因子整体均值为负，说明是反向因子（低值更好），取尾部
        if latest.mean() < 0:
            selected = latest.nsmallest(10)
        else:
            selected = latest.nlargest(10)

        # 获取股票名称
        stock_info = data.get_stock_info()
        name_map = (
            dict(zip(stock_info["symbol"], stock_info["name"]))
            if "symbol" in stock_info.columns
            else {}
        )

        rows = []
        for sym, score in selected.items():
            rows.append(
                {
                    "symbol": sym,
                    "name": name_map.get(sym, ""),
                    "score": round(float(score), 4),
                    "reason": f"GP因子得分 {score:.4f}",
                    "confidence": 0.6,
                }
            )

        return pd.DataFrame(rows)

    def to_dict(self):
        d = super().to_dict()
        d["factor_expr"] = str(self.factor_expr) if self.factor_expr else None
        return d
