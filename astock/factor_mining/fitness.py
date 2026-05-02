"""
适应度函数
==========
"""
import numpy as np
import pandas as pd


def _spearman_corr(a: pd.Series, b: pd.Series) -> float:
    """不依赖 scipy 的 Spearman 秩相关"""
    ra = a.rank()
    rb = b.rank()
    return ra.corr(rb)


def evaluate_ic(factor_df: pd.DataFrame, label_df: pd.DataFrame):
    """
    计算 Rank IC 序列和 IR

    Returns
    -------
    mean_ic : float
        平均 Rank IC
    ir : float
        信息比率 IC_mean / IC_std
    ics : list[float]
        每日 IC 序列
    """
    ics = []
    common_dates = factor_df.index.intersection(label_df.index)

    for date in common_dates:
        f = factor_df.loc[date].dropna()
        l = label_df.loc[date].dropna()
        common_syms = f.index.intersection(l.index)
        if len(common_syms) < 10:
            continue
        ic = _spearman_corr(f[common_syms], l[common_syms])
        if pd.notna(ic):
            ics.append(ic)

    if len(ics) < 10:
        return 0.0, 0.0, []

    mean_ic = float(np.mean(ics))
    std_ic = float(np.std(ics)) + 1e-8
    ir = mean_ic / std_ic
    return mean_ic, ir, ics
