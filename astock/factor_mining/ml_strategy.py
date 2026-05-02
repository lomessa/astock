"""
机器学习多因子选股策略
======================
基于训练好的回归模型，用多因子特征预测 T+3 收益并选股。
"""
import pandas as pd
import numpy as np
from deap import gp

from astock.strategy.base import StrategyBase
from .data_prep import prepare_data_from_db
from .feature_engine import build_pset, compute_factor
from .model_trainer import load_model_bundle


class MLFactorStrategy(StrategyBase):
    """
    多因子 ML 预测选股策略

    Parameters
    ----------
    model_path : str
        模型 bundle 路径（joblib 保存）
    top_n : int
        每日选股数量
    lookback_days : int
        准备历史数据时的回看天数
    """

    name = "ml_factor"

    def __init__(self, model_path: str = None, top_n: int = 10, lookback_days: int = 30):
        self.model_path = model_path
        self.top_n = top_n
        self.lookback_days = lookback_days
        self._bundle = None
        self._load_bundle()

    def _load_bundle(self):
        if self.model_path is None:
            import os
            self.model_path = os.path.expanduser("~/.astock/ml_model.pkl")
        try:
            self._bundle = load_model_bundle(self.model_path)
        except Exception as e:
            print(f"[MLStrategy] 加载模型失败: {e}")
            self._bundle = None

    def select_candidates(self, data, trade_date: str) -> pd.DataFrame:
        if self._bundle is None:
            return pd.DataFrame()

        model = self._bundle["model"]
        factor_exprs = self._bundle["factor_exprs"]
        feature_cols = self._bundle.get("feature_cols", [f"f_{i}" for i in range(len(factor_exprs))])

        # 准备近 N 天数据
        data_dict, _ = prepare_data_from_db(trade_date, lookback_days=self.lookback_days, forward_period=3)
        if data_dict is None:
            return pd.DataFrame()

        pset = build_pset(data_dict)

        # 计算每个因子在最新截面的值
        feat_rows = []
        for i, expr in enumerate(factor_exprs):
            col = f"f_{i}"
            if col not in feature_cols:
                continue
            factor_wide = compute_factor(data_dict, expr, pset)
            if factor_wide is None or factor_wide.empty:
                continue
            latest = factor_wide.iloc[-1].dropna()
            if latest.empty:
                continue
            # 替换 inf 为 NaN 再计算 zscore
            latest = latest.replace([np.inf, -np.inf], np.nan).dropna()
            if latest.empty or latest.std() == 0:
                continue
            latest_z = (latest - latest.mean()) / (latest.std() + 1e-8)
            latest_z = latest_z.clip(-3, 3)
            for sym, val in latest_z.items():
                feat_rows.append({"symbol": sym, col: val})

        if not feat_rows:
            return pd.DataFrame()

        feat_df = pd.DataFrame(feat_rows)
        # 同一 symbol 多列合并
        feat_df = feat_df.groupby("symbol").first().reset_index()

        # 确保所有特征列存在
        for col in feature_cols:
            if col not in feat_df.columns:
                feat_df[col] = 0.0

        # 去掉含缺失值的行
        feat_df = feat_df.dropna(subset=feature_cols)
        if feat_df.empty:
            return pd.DataFrame()

        X = feat_df[feature_cols].values
        preds = model.predict(X)
        feat_df["pred_return"] = preds

        # 取预测得分最高的 top_n
        selected = feat_df.nlargest(self.top_n, "pred_return")

        # 获取股票名称
        stock_info = data.get_stock_info()
        name_map = (
            dict(zip(stock_info["symbol"], stock_info["name"]))
            if "symbol" in stock_info.columns
            else {}
        )

        rows = []
        for _, row in selected.iterrows():
            pred = float(row["pred_return"])
            # confidence 随预测收益递增，限定在 0.5~0.95
            confidence = min(0.95, max(0.5, 0.6 + pred * 5))
            rows.append(
                {
                    "symbol": row["symbol"],
                    "name": name_map.get(row["symbol"], ""),
                    "score": round(pred, 4),
                    "reason": f"ML模型预测3日收益 {pred:+.4f}",
                    "confidence": round(confidence, 3),
                    "strategy": self.name,
                }
            )

        return pd.DataFrame(rows)

    def to_dict(self):
        d = super().to_dict()
        d["model_path"] = self.model_path
        d["top_n"] = self.top_n
        return d
