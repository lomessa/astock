"""
多因子合成 + 中性化
==================
支持等权、IC 加权、IR 加权合成，以及市值/行业中性化。
"""
import sqlite3
import numpy as np
import pandas as pd

from astock.config import get_settings


def load_mv_from_db(end_date: str) -> pd.Series:
    """
    从 stock_info 加载流通市值（亿元），作为中性化 regressors
    """
    cfg = get_settings().data
    conn = sqlite3.connect(cfg.db_path)
    df = pd.read_sql_query(
        "SELECT symbol, float_mv FROM stock_info WHERE float_mv IS NOT NULL",
        conn,
    )
    conn.close()
    if df.empty:
        return pd.Series(dtype=float)
    return df.set_index("symbol")["float_mv"]


def neutralize(
    factor: pd.Series,
    mv: pd.Series = None,
    industry: pd.Series = None,
) -> pd.Series:
    """
    对因子进行中性化处理（截面回归取残差）

    Parameters
    ----------
    factor : pd.Series
        index=symbol, values=factor_value（已做好截面预处理如 rank/zscore）
    mv : pd.Series
        流通市值（取 log），index=symbol
    industry : pd.Series
        行业类别（dummy），index=symbol（当前数据不全，预留接口）

    Returns
    -------
    pd.Series : 中性化后的因子残差
    """
    df = pd.DataFrame({"factor": factor}).dropna()
    if df.empty:
        return factor

    regressors = []

    if mv is not None:
        mv_aligned = mv.reindex(df.index).dropna()
        if not mv_aligned.empty:
            log_mv = np.log(mv_aligned)
            # zscore
            log_mv = (log_mv - log_mv.mean()) / (log_mv.std() + 1e-8)
            df = df.loc[mv_aligned.index].copy()
            df["log_mv"] = log_mv
            regressors.append("log_mv")

    if industry is not None:
        ind_aligned = industry.reindex(df.index).dropna()
        if not ind_aligned.empty:
            df = df.loc[ind_aligned.index].copy()
            dummies = pd.get_dummies(ind_aligned, prefix="ind")
            # 去掉一个基准行业避免多重共线性
            if dummies.shape[1] > 1:
                dummies = dummies.iloc[:, 1:]
            df = pd.concat([df, dummies], axis=1)
            regressors.extend(dummies.columns.tolist())

    if not regressors:
        return factor

    # OLS 残差
    X = df[regressors].values
    y = df["factor"].values
    # 添加截距
    X = np.column_stack([np.ones(len(X)), X])
    try:
        beta = np.linalg.lstsq(X, y, rcond=None)[0]
        resid = y - X @ beta
    except Exception:
        return factor

    result = pd.Series(resid, index=df.index, name=factor.name)
    return result


def combine_factors(
    factor_dict: dict[str, pd.DataFrame],
    weights: dict[str, float] = None,
    method: str = "equal",
    label_df: pd.DataFrame = None,
    neutralize_mv: bool = True,
    mv_series: pd.Series = None,
) -> pd.DataFrame:
    """
    多因子合成

    Parameters
    ----------
    factor_dict : dict[str, pd.DataFrame]
        因子名 -> 宽格式 DataFrame（index=date, columns=symbol）
    weights : dict[str, float]
        手动指定权重（若 method='custom'）
    method : str
        'equal' | 'ic' | 'ir' | 'custom'
    label_df : pd.DataFrame
        收益标签，用于计算 IC/IR 加权（method='ic'/'ir' 时必须）
    neutralize_mv : bool
        是否对每个因子先做市值中性化
    mv_series : pd.Series
        流通市值，index=symbol

    Returns
    -------
    pd.DataFrame : 合成后的因子（index=date, columns=symbol）
    """
    if not factor_dict:
        return pd.DataFrame()

    # 统一日期和股票
    common_dates = None
    common_syms = None
    for fdf in factor_dict.values():
        if common_dates is None:
            common_dates = set(fdf.index)
            common_syms = set(fdf.columns)
        else:
            common_dates &= set(fdf.index)
            common_syms &= set(fdf.columns)

    common_dates = sorted(common_dates)
    common_syms = sorted(common_syms)

    aligned_factors = {}
    for name, fdf in factor_dict.items():
        aligned = fdf.reindex(index=common_dates, columns=common_syms)
        # 截面 rank 标准化到 0~1
        aligned = aligned.rank(axis=1, pct=True)
        aligned_factors[name] = aligned

    # 计算权重
    if method == "equal":
        w = {name: 1.0 / len(aligned_factors) for name in aligned_factors}
    elif method == "custom":
        w = weights or {name: 1.0 / len(aligned_factors) for name in aligned_factors}
    elif method in ("ic", "ir"):
        if label_df is None:
            raise ValueError("IC/IR 加权需要提供 label_df")
        w = {}
        for name, fdf in aligned_factors.items():
            ics = []
            for date in common_dates:
                if date not in label_df.index:
                    continue
                f = fdf.loc[date].dropna()
                l = label_df.loc[date].dropna()
                common = f.index.intersection(l.index)
                if len(common) < 10:
                    continue
                ic = f[common].corr(l[common])
                if pd.notna(ic):
                    ics.append(ic)
            if not ics:
                w[name] = 0.0
                continue
            mean_ic = np.mean(ics)
            std_ic = np.std(ics) + 1e-8
            ir = mean_ic / std_ic
            if method == "ic":
                w[name] = abs(mean_ic)
            else:
                w[name] = max(abs(ir), 0)
        total = sum(w.values()) + 1e-8
        w = {k: v / total for k, v in w.items()}
    else:
        raise ValueError(f"未知合成方法: {method}")

    # 逐个因子市值中性化（可选）
    if neutralize_mv:
        if mv_series is None:
            # 尝试从 DB 加载（但截面每天市值会变，这里用最新截面近似）
            # 更精确的做法是从 daily 表计算，但 stock_info 里的 float_mv 是近似值
            pass
        for name in list(aligned_factors.keys()):
            fdf = aligned_factors[name]
            neutralized = []
            for date in fdf.index:
                row = fdf.loc[date]
                if mv_series is not None and not mv_series.empty:
                    resid = neutralize(row, mv=mv_series)
                else:
                    resid = row
                neutralized.append(resid)
            aligned_factors[name] = pd.DataFrame(neutralized, index=fdf.index, columns=fdf.columns)
            # 再次 rank
            aligned_factors[name] = aligned_factors[name].rank(axis=1, pct=True)

    # 加权合成
    combined = pd.DataFrame(0.0, index=common_dates, columns=common_syms)
    for name, fdf in aligned_factors.items():
        weight = w.get(name, 0.0)
        if weight <= 0:
            continue
        combined += fdf.fillna(0) * weight

    return combined
