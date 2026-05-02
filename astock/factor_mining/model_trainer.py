"""
多因子模型训练
==============
基于 GP 挖掘的多因子特征，训练机器学习回归模型预测 T+3 收益。
"""
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_squared_error, r2_score
import joblib

from .feature_engine import get_feature_columns


MODEL_REGISTRY = {
    "gbdt": HistGradientBoostingRegressor,
    "rf": RandomForestRegressor,
    "ridge": Ridge,
}


def create_model(model_type: str = "gbdt", **kwargs):
    """创建模型实例"""
    cls = MODEL_REGISTRY.get(model_type)
    if cls is None:
        raise ValueError(f"未知模型类型: {model_type}，可选: {list(MODEL_REGISTRY.keys())}")
    # 默认参数
    defaults = {}
    if model_type == "gbdt":
        defaults = {"max_iter": 200, "learning_rate": 0.05, "max_depth": 4}
    elif model_type == "rf":
        defaults = {"n_estimators": 200, "max_depth": 6, "min_samples_leaf": 50}
    elif model_type == "ridge":
        defaults = {"alpha": 1.0}
    defaults.update(kwargs)
    return cls(**defaults)


def train_model(
    feature_df: pd.DataFrame,
    model_type: str = "gbdt",
    label_col: str = "label",
    test_size: float = 0.2,
    random_state: int = 42,
    **model_kwargs,
) -> Tuple[object, Dict]:
    """
    在固定数据集上训练模型。

    Returns
    -------
    model : sklearn estimator
    metrics : dict
        包含 train_rmse, test_rmse, train_r2, test_r2, ic, ir
    """
    feat_cols = get_feature_columns(feature_df)
    if not feat_cols:
        raise ValueError("特征矩阵无有效特征列")

    df = feature_df.copy()
    df = df.sort_values(["date", "symbol"]).reset_index(drop=True)

    # 按时间切分训练/测试集（避免未来函数）
    dates = df["date"].unique()
    n_train = max(int(len(dates) * (1 - test_size)), 1)
    train_dates = dates[:n_train]
    test_dates = dates[n_train:]

    train_df = df[df["date"].isin(train_dates)]
    test_df = df[df["date"].isin(test_dates)] if len(test_dates) > 0 else train_df

    X_train = train_df[feat_cols].values
    y_train = train_df[label_col].values
    X_test = test_df[feat_cols].values
    y_test = test_df[label_col].values

    model = create_model(model_type, **model_kwargs)
    model.fit(X_train, y_train)

    # 预测
    train_pred = model.predict(X_train)
    test_pred = model.predict(X_test)

    # 日度 IC（测试集）
    test_df = test_df.copy()
    test_df["pred"] = test_pred
    daily_ic = []
    for date, g in test_df.groupby("date"):
        if len(g) < 10:
            continue
        ic = g["pred"].corr(g[label_col])
        if pd.notna(ic):
            daily_ic.append(ic)
    mean_ic = float(np.mean(daily_ic)) if daily_ic else 0.0
    ir = float(mean_ic / (np.std(daily_ic) + 1e-8)) if daily_ic else 0.0

    metrics = {
        "train_rmse": float(np.sqrt(mean_squared_error(y_train, train_pred))),
        "test_rmse": float(np.sqrt(mean_squared_error(y_test, test_pred))),
        "train_r2": float(r2_score(y_train, train_pred)),
        "test_r2": float(r2_score(y_test, test_pred)),
        "ic": round(mean_ic, 4),
        "ir": round(ir, 4),
        "ic_days": len(daily_ic),
    }

    # 特征重要性
    if hasattr(model, "feature_importances_"):
        metrics["feature_importance"] = {
            feat_cols[i]: round(v, 4)
            for i, v in enumerate(model.feature_importances_)
        }
    elif hasattr(model, "coef_"):
        metrics["feature_importance"] = {
            feat_cols[i]: round(v, 4)
            for i, v in enumerate(model.coef_.flatten())
        }

    return model, metrics


def walk_forward_predict(
    feature_df: pd.DataFrame,
    model_type: str = "gbdt",
    train_window: int = 60,
    predict_window: int = 5,
    label_col: str = "label",
    **model_kwargs,
) -> pd.DataFrame:
    """
    Walk-forward 回测：滚动训练、滚动预测。

    Parameters
    ----------
    feature_df : pd.DataFrame
        完整特征矩阵（含 label）
    train_window : int
        每次训练使用的交易日数量
    predict_window : int
        每次预测 forward 多少个交易日

    Returns
    -------
    pd.DataFrame
        columns: [date, symbol, actual, predicted]
    """
    feat_cols = get_feature_columns(feature_df)
    if not feat_cols:
        raise ValueError("特征矩阵无有效特征列")

    df = feature_df.copy().sort_values("date").reset_index(drop=True)
    dates = sorted(df["date"].unique())

    records = []
    i = train_window
    while i + predict_window <= len(dates):
        train_dates = dates[i - train_window:i]
        predict_dates = dates[i:i + predict_window]

        train_df = df[df["date"].isin(train_dates)]
        predict_df = df[df["date"].isin(predict_dates)].copy()

        if train_df.empty or predict_df.empty:
            i += predict_window
            continue

        X_train = train_df[feat_cols].values
        y_train = train_df[label_col].values
        X_pred = predict_df[feat_cols].values

        model = create_model(model_type, **model_kwargs)
        model.fit(X_train, y_train)
        pred = model.predict(X_pred)

        predict_df["predicted"] = pred
        records.append(predict_df[["date", "symbol", label_col, "predicted"]])

        i += predict_window

    if not records:
        return pd.DataFrame(columns=["date", "symbol", "actual", "predicted"])

    result = pd.concat(records, ignore_index=True)
    result = result.rename(columns={label_col: "actual"})
    return result


def save_model_bundle(
    model,
    factor_exprs: List[str],
    feature_cols: List[str],
    metrics: Dict,
    save_path: str,
):
    """
    保存模型 + 因子表达式 + 元数据

    Parameters
    ----------
    model : sklearn estimator
    factor_exprs : list[str]
        训练时使用的因子表达式列表
    feature_cols : list[str]
        特征列名（f_0, f_1, ...）
    metrics : dict
        训练指标
    save_path : str
        保存路径（如 ~/.astock/ml_model.pkl）
    """
    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        "model": model,
        "factor_exprs": factor_exprs,
        "feature_cols": feature_cols,
        "metrics": metrics,
        "version": "1.0",
    }
    joblib.dump(bundle, save_path)
    print(f"[ModelTrainer] 模型已保存到 {save_path}")


def load_model_bundle(save_path: str) -> Dict:
    """加载模型 bundle"""
    return joblib.load(save_path)
