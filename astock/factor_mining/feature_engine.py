"""
多因子特征工程
==============
将 GP 挖掘出的多个因子表达式转换为可用于机器学习模型的特征矩阵。
"""
import json
from functools import partial
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from deap import gp

from . import operators


def load_gp_factors(
    factor_path: Optional[str] = None,
    top_n: Optional[int] = None,
    min_quality_pass: bool = False,
) -> List[dict]:
    path = factor_path or str(Path.home() / ".astock" / "gp_factors.json")
    try:
        with open(path, "r") as f:
            factors = json.load(f)
    except Exception:
        return []

    if min_quality_pass:
        factors = [f for f in factors if f.get("quality_pass", False)]

    factors = sorted(factors, key=lambda x: x.get("fitness", 0), reverse=True)

    if top_n is not None:
        factors = factors[:top_n]

    return factors


def build_pset(data_dict: Dict[str, pd.DataFrame]) -> gp.PrimitiveSet:
    import random

    pset = gp.PrimitiveSet("MAIN", 0)

    for name in data_dict.keys():
        pset.addTerminal(name, name=name)
        pset.context[name] = data_dict[name]

    pset.addEphemeralConstant("rand", partial(lambda: random.uniform(-1, 1)))

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


def compute_factor(
    data_dict: Dict[str, pd.DataFrame],
    expr_str: str,
    pset: gp.PrimitiveSet,
) -> Optional[pd.DataFrame]:
    try:
        expr = gp.PrimitiveTree.from_string(expr_str, pset)
        factor_df = gp.compile(expr=expr, pset=pset)
    except Exception as e:
        print(f"[FeatureEngine] 因子编译失败: {expr_str} | {e}")
        return None

    if not isinstance(factor_df, pd.DataFrame) or factor_df.empty:
        return None

    # 处理极端值（inf -> NaN）
    factor_df = factor_df.replace([np.inf, -np.inf], np.nan)
    return factor_df


def cs_zscore_clip(df: pd.DataFrame, clip: float = 3.0) -> pd.DataFrame:
    mean = df.mean(axis=1)
    std = df.std(axis=1) + 1e-8
    z = df.sub(mean, axis=0).div(std, axis=0)
    if clip is not None:
        z = z.clip(lower=-clip, upper=clip)
    return z


def wide_to_long(
    wide_df: pd.DataFrame,
    value_name: str,
) -> pd.DataFrame:
    df = wide_df.copy()
    df["date"] = df.index
    long_df = df.melt(id_vars=["date"], var_name="symbol", value_name=value_name)
    long_df = long_df.dropna()
    return long_df


def build_feature_matrix(
    data_dict: Dict[str, pd.DataFrame],
    factor_exprs: List[str],
    label_df: Optional[pd.DataFrame] = None,
    pset: Optional[gp.PrimitiveSet] = None,
) -> pd.DataFrame:
    if pset is None:
        pset = build_pset(data_dict)

    long_parts = []

    for i, expr in enumerate(factor_exprs):
        factor_wide = compute_factor(data_dict, expr, pset)
        if factor_wide is None:
            continue
        factor_z = cs_zscore_clip(factor_wide)
        factor_long = wide_to_long(factor_z, value_name=f"f_{i}")
        long_parts.append(factor_long.set_index(["date", "symbol"]))

    if not long_parts:
        print("[FeatureEngine] 无有效因子，无法构建特征矩阵")
        return pd.DataFrame()

    # outer join 保留尽可能多的样本；缺失因子填充 0（表示截面均值）
    feature_df = pd.concat(long_parts, axis=1)
    feature_df = feature_df.fillna(0).reset_index()

    if label_df is not None:
        label_long = wide_to_long(label_df, value_name="label")
        feature_df = feature_df.merge(label_long, on=["date", "symbol"], how="inner")

    # 仅去掉没有 label 的行
    if "label" in feature_df.columns:
        feature_df = feature_df.dropna(subset=["label"])

    return feature_df


def get_feature_columns(feature_df: pd.DataFrame) -> List[str]:
    return [c for c in feature_df.columns if c.startswith("f_")]
