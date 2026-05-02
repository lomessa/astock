"""
GP 因子增强器
=============
将遗传规划发现的因子应用到候选股票池上，作为现有策略的过滤/打分层。
"""
import json
from pathlib import Path
from typing import Optional, List

import pandas as pd
import numpy as np
from deap import gp

from .data_prep import prepare_data_from_db
from .gp_engine import FactorGPEngine


class GPFactorBooster:
    """
    GP 因子增强器

    用法：
        booster = GPFactorBooster()
        factor_values = booster.compute(trade_date='20260428')
        # factor_values: Series(index=symbol, values=factor_score)
    """

    def __init__(self, factor_path: Optional[str] = None):
        self.factor_path = factor_path or str(Path.home() / ".astock" / "gp_factors.json")
        self.factors = self._load_factors()
        self.pset = None  # 延迟构建

    def _load_factors(self) -> list:
        try:
            with open(self.factor_path, "r") as f:
                return json.load(f)
        except Exception:
            return []

    def _build_pset(self, data_dict):
        """基于当前数据构建 PrimitiveSet（轻量版）"""
        from . import operators
        from functools import partial
        import random

        pset = gp.PrimitiveSet("MAIN", 0)

        # Terminals
        for name in data_dict.keys():
            pset.addTerminal(name, name=name)
            pset.context[name] = data_dict[name]

        pset.addEphemeralConstant("rand", partial(lambda: random.uniform(-1, 1)))

        # Primitives（与 gp_engine 保持一致）
        for w in [5, 10, 20]:
            pset.addPrimitive(partial(operators.ts_mean, window=w), 1, name=f"ts_mean_{w}")
            pset.addPrimitive(partial(operators.ts_std, window=w), 1, name=f"ts_std_{w}")
            pset.addPrimitive(partial(operators.ts_max, window=w), 1, name=f"ts_max_{w}")
            pset.addPrimitive(partial(operators.ts_min, window=w), 1, name=f"ts_min_{w}")
        for w in [1, 5]:
            pset.addPrimitive(partial(operators.ts_delay, window=w), 1, name=f"ts_delay_{w}")
            pset.addPrimitive(partial(operators.ts_delta, window=w), 1, name=f"ts_delta_{w}")
        for w in [5, 10]:
            pset.addPrimitive(partial(operators.ts_rank, window=w), 1, name=f"ts_rank_{w}")

        pset.addPrimitive(operators.cs_rank, 1, name="cs_rank")
        pset.addPrimitive(operators.cs_zscore, 1, name="cs_zscore")
        pset.addPrimitive(operators.neg, 1, name="neg")
        pset.addPrimitive(operators.abs_op, 1, name="abs")
        pset.addPrimitive(operators.sign, 1, name="sign")
        pset.addPrimitive(operators.log_op, 1, name="log")
        pset.addPrimitive(operators.add, 2, name="add")
        pset.addPrimitive(operators.sub, 2, name="sub")
        pset.addPrimitive(operators.mul, 2, name="mul")
        pset.addPrimitive(operators.div, 2, name="div")
        pset.addPrimitive(operators.max_op, 2, name="max")
        pset.addPrimitive(operators.min_op, 2, name="min")
        for w in [5, 10]:
            pset.addPrimitive(partial(operators.ts_corr, window=w), 2, name=f"ts_corr_{w}")

        return pset

    def compute(self, trade_date: str, lookback_days: int = 30, forward_period: int = 3) -> Optional[pd.Series]:
        """
        计算最优 GP 因子在 trade_date 的最新值

        Parameters
        ----------
        trade_date : str
        lookback_days : int
        forward_period : int
            数据准备时的预测周期（默认 3）

        Returns
        -------
        pd.Series | None
            index=symbol, values=factor_value
        """
        if not self.factors:
            print("[GPBooster] 无保存的 GP 因子，跳过")
            return None

        # 取最优因子
        top_factor = max(self.factors, key=lambda x: x.get("fitness", 0))
        expr_str = top_factor.get("expr", "")
        direction = top_factor.get("direction", "forward")

        if not expr_str:
            return None

        # 准备数据
        data_dict, _ = prepare_data_from_db(trade_date, lookback_days=lookback_days, forward_period=forward_period)
        if data_dict is None:
            return None

        # 构建 pset 并编译
        pset = self._build_pset(data_dict)
        try:
            expr = gp.PrimitiveTree.from_string(expr_str, pset)
            factor_df = gp.compile(expr=expr, pset=pset)
        except Exception as e:
            print(f"[GPBooster] 因子编译失败: {e}")
            return None

        if not isinstance(factor_df, pd.DataFrame) or factor_df.empty:
            return None

        # 取最新一天
        latest = factor_df.iloc[-1].dropna()
        if latest.empty:
            return None

        return latest

    def filter_candidates(
        self,
        candidates: pd.DataFrame,
        trade_date: str,
        top_pct: float = 0.5,
        score_boost: float = 0.15,
        forward_period: int = 3,
    ) -> pd.DataFrame:
        """
        对候选股票池进行 GP 因子过滤/增强

        Parameters
        ----------
        candidates : pd.DataFrame
            必须包含 symbol 列
        trade_date : str
        top_pct : float
            保留因子值排名前多少比例（0.5=前50%）
        score_boost : float
            对 GP 因子排名靠前的股票，confidence 额外加多少
        forward_period : int
            预测周期

        Returns
        -------
        pd.DataFrame
        """
        if candidates.empty or "symbol" not in candidates.columns:
            return candidates

        factor = self.compute(trade_date, forward_period=forward_period)
        if factor is None:
            return candidates

        # 将因子值合并到候选池
        factor_df = factor.reset_index()
        factor_df.columns = ["symbol", "gp_factor"]
        merged = candidates.merge(factor_df, on="symbol", how="left")

        # 过滤掉没有因子值或排名太低的
        # 方向判断：如果最优因子是 reverse（IC<0），则因子值越小越好
        top_factor = max(self.factors, key=lambda x: x.get("fitness", 0))
        direction = top_factor.get("direction", "forward")

        valid = merged.dropna(subset=["gp_factor"])
        if valid.empty:
            return candidates

        if direction == "reverse":
            # 越小越好，取 bottom top_pct
            threshold = valid["gp_factor"].quantile(top_pct)
            filtered = valid[valid["gp_factor"] <= threshold].copy()
        else:
            threshold = valid["gp_factor"].quantile(1 - top_pct)
            filtered = valid[valid["gp_factor"] >= threshold].copy()

        # 对保留的股票，提升 confidence
        if "confidence" in filtered.columns:
            filtered["confidence"] = (filtered["confidence"] + score_boost).clip(upper=1.0)
        else:
            filtered["confidence"] = score_boost

        # 把 GP 因子信息写入 reason
        if "reason" in filtered.columns:
            filtered["reason"] = filtered.apply(
                lambda r: f"{r['reason']} | GP因子:{r['gp_factor']:.4f}", axis=1
            )

        # 标记策略来源
        if "strategy" in filtered.columns:
            filtered["strategy"] = filtered["strategy"] + "+GP"
        else:
            filtered["strategy"] = "GPBoosted"

        return filtered
