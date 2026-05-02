"""
因子质量评估体系
===============
对单个因子进行全面体检，判断其是否可用于实盘。
"""
import math
import numpy as np
import pandas as pd


def _ttest_1samp(values: pd.Series) -> tuple:
    """单样本 t 检验（替代 scipy）"""
    n = len(values)
    if n < 2:
        return 0.0, 1.0
    mean = values.mean()
    std = values.std(ddof=1)
    if std == 0:
        return 0.0, 1.0
    t_stat = mean / (std / math.sqrt(n))
    # 正态近似 p 值（双侧）
    p_value = 2 * (1 - _normal_cdf(abs(t_stat)))
    return t_stat, p_value


def _normal_cdf(x: float) -> float:
    """标准正态累积分布函数近似（Abramowitz & Stegun）"""
    b1 = 0.319381530
    b2 = -0.356563782
    b3 = 1.781477937
    b4 = -1.821255978
    b5 = 1.330274429
    p = 0.2316419
    c = 0.39894228

    if x >= 0.0:
        t = 1.0 / (1.0 + p * x)
        return 1.0 - c * math.exp(-x * x / 2.0) * t * (t * (t * (t * (t * b5 + b4) + b3) + b2) + b1)
    else:
        return 1.0 - _normal_cdf(-x)


def evaluate_factor_quality(
    factor_df: pd.DataFrame,
    label_df: pd.DataFrame,
    turnover_threshold: float = 0.7,
    min_coverage: float = 0.5,
) -> dict:
    """
    全面评估单个因子的质量

    Parameters
    ----------
    factor_df : pd.DataFrame
        因子值（index=date, columns=symbol）
    label_df : pd.DataFrame
        收益标签（index=date, columns=symbol）
    turnover_threshold : float
        日换手率上限（因子值日序列的自相关系数），低于此值认为换手太高
    min_coverage : float
        最小覆盖率（有效因子值 / 总股票数）

    Returns
    -------
    dict : 质量报告
    """
    report = {
        "pass": False,
        "ic": {},
        "turnover": {},
        "coverage": {},
        "group": {},
        "decay": {},
        "warnings": [],
    }

    common_dates = factor_df.index.intersection(label_df.index)
    if len(common_dates) < 20:
        report["warnings"].append(f"共同交易日不足: {len(common_dates)} < 20")
        return report

    # ---------- 1. IC 序列与显著性 ----------
    ics = []
    for date in common_dates:
        f = factor_df.loc[date].dropna()
        l = label_df.loc[date].dropna()
        common_syms = f.index.intersection(l.index)
        if len(common_syms) < 10:
            continue
        ic = f[common_syms].rank().corr(l[common_syms].rank())
        if pd.notna(ic):
            ics.append(ic)

    if len(ics) < 10:
        report["warnings"].append("有效 IC 天数不足")
        return report

    ics = pd.Series(ics)
    mean_ic = ics.mean()
    std_ic = ics.std()
    ir = mean_ic / (std_ic + 1e-8)
    t_stat, p_value = _ttest_1samp(ics)

    report["ic"]["mean"] = round(mean_ic, 4)
    report["ic"]["std"] = round(std_ic, 4)
    report["ic"]["ir"] = round(ir, 4)
    report["ic"]["t_stat"] = round(t_stat, 4) if pd.notna(t_stat) else None
    report["ic"]["p_value"] = round(p_value, 4) if pd.notna(p_value) else None
    report["ic"]["positive_ratio"] = round((ics > 0).mean(), 4)
    report["ic"]["days"] = len(ics)

    # IC 显著性判断：|t| > 2 且 p < 0.05
    ic_significant = abs(t_stat) > 2 and p_value < 0.05 if pd.notna(t_stat) and pd.notna(p_value) else False
    if not ic_significant:
        report["warnings"].append(f"IC 不显著 (t={t_stat:.2f}, p={p_value:.3f})")

    # ---------- 2. 覆盖率 ----------
    total_cells = factor_df.shape[0] * factor_df.shape[1]
    valid_cells = total_cells - factor_df.isna().sum().sum()
    coverage_ratio = valid_cells / (total_cells + 1e-8)
    report["coverage"]["ratio"] = round(coverage_ratio, 4)
    if coverage_ratio < min_coverage:
        report["warnings"].append(f"覆盖率过低: {coverage_ratio:.1%} < {min_coverage:.0%}")

    # ---------- 3. 换手率（因子稳定性） ----------
    turnover_corrs = []
    for i in range(1, len(factor_df)):
        prev = factor_df.iloc[i - 1].dropna()
        curr = factor_df.iloc[i].dropna()
        common = prev.index.intersection(curr.index)
        if len(common) < 10:
            continue
        corr = prev[common].rank().corr(curr[common].rank())
        if pd.notna(corr):
            turnover_corrs.append(corr)

    if turnover_corrs:
        mean_turnover_corr = np.mean(turnover_corrs)
        report["turnover"]["mean_auto_corr"] = round(mean_turnover_corr, 4)
        report["turnover"]["days"] = len(turnover_corrs)
        if mean_turnover_corr < turnover_threshold:
            report["warnings"].append(
                f"换手率太高 (日秩自相关系数={mean_turnover_corr:.3f} < {turnover_threshold})"
            )
    else:
        report["turnover"]["mean_auto_corr"] = None
        report["warnings"].append("无法计算换手率（数据不足）")

    # ---------- 4. 分组收益单调性 ----------
    group_rets = []
    n_groups = 5
    for date in common_dates:
        f = factor_df.loc[date].dropna()
        l = label_df.loc[date].dropna()
        common_syms = f.index.intersection(l.index)
        if len(common_syms) < n_groups * 4:
            continue
        df_day = pd.DataFrame({"factor": f[common_syms], "label": l[common_syms]}).dropna()
        if len(df_day) < n_groups * 4:
            continue
        try:
            df_day["group"] = pd.qcut(df_day["factor"], n_groups, labels=False, duplicates="drop")
        except ValueError:
            continue
        day_rets = df_day.groupby("group")["label"].mean()
        if len(day_rets) == n_groups:
            group_rets.append(day_rets)

    if group_rets:
        group_df = pd.DataFrame(group_rets)
        group_means = group_df.mean()
        report["group"]["mean_ret"] = {int(k): round(v, 6) for k, v in group_means.items()}
        report["group"]["long_short"] = round(group_means.iloc[-1] - group_means.iloc[0], 6)
        # 单调性：计算 group 编号与收益的秩相关系数
        mono_corr = pd.Series(group_means.index).corr(pd.Series(group_means.values))
        report["group"]["monotonicity"] = round(mono_corr, 4) if pd.notna(mono_corr) else None
        if mono_corr is not None and abs(mono_corr) < 0.3:
            report["warnings"].append(f"分组单调性弱 (rho={mono_corr:.2f})")
    else:
        report["warnings"].append("分组测试数据不足")

    # ---------- 5. IC 衰减（半衰期） ----------
    decay_lags = [1, 2, 3, 5, 10]
    decay_ics = {}
    for lag in decay_lags:
        lag_ics = []
        for i in range(len(factor_df) - lag):
            date = factor_df.index[i]
            future_date = factor_df.index[i + lag]
            if future_date not in label_df.index:
                continue
            f = factor_df.loc[date].dropna()
            l = label_df.loc[future_date].dropna()
            common_syms = f.index.intersection(l.index)
            if len(common_syms) < 10:
                continue
            ic = f[common_syms].rank().corr(l[common_syms].rank())
            if pd.notna(ic):
                lag_ics.append(ic)
        if lag_ics:
            decay_ics[lag] = round(np.mean(lag_ics), 4)
    report["decay"] = decay_ics

    # ---------- 6. 综合判定 ----------
    report["pass"] = (
        ic_significant
        and coverage_ratio >= min_coverage
        and (report["turnover"].get("mean_auto_corr") is None or report["turnover"]["mean_auto_corr"] >= turnover_threshold)
        and (report["group"].get("monotonicity") is None or abs(report["group"]["monotonicity"]) >= 0.3)
    )

    return report


def format_quality_report(report: dict) -> str:
    """格式化质量报告为可读字符串"""
    lines = []
    lines.append("=" * 60)
    lines.append("因子质量评估报告")
    lines.append("=" * 60)

    status = "✅ 通过" if report["pass"] else "❌ 不通过"
    lines.append(f"综合判定: {status}")
    lines.append("")

    ic = report.get("ic", {})
    lines.append(f"IC 均值:    {ic.get('mean')} | IR: {ic.get('ir')} | 天数: {ic.get('days')}")
    lines.append(f"IC 正占比:  {ic.get('positive_ratio')}")
    lines.append(f"T 检验:     t={ic.get('t_stat')}, p={ic.get('p_value')}")
    lines.append("")

    cov = report.get("coverage", {})
    lines.append(f"覆盖率:     {cov.get('ratio')}")
    lines.append("")

    to = report.get("turnover", {})
    lines.append(f"日秩自相关: {to.get('mean_auto_corr')} (越高越稳定)")
    lines.append("")

    grp = report.get("group", {})
    if grp.get("mean_ret"):
        lines.append("五分组日均收益:")
        for g, r in grp["mean_ret"].items():
            lines.append(f"  组{g}: {r:+.4%}")
        lines.append(f"多空收益:   {grp.get('long_short'):+.4%}")
        lines.append(f"单调性:     {grp.get('monotonicity')}")
    lines.append("")

    decay = report.get("decay", {})
    if decay:
        lines.append("IC 衰减:")
        for lag, ic_val in decay.items():
            lines.append(f"  滞后{lag}天: {ic_val}")
    lines.append("")

    if report.get("warnings"):
        lines.append("⚠️ 警告:")
        for w in report["warnings"]:
            lines.append(f"  - {w}")
    else:
        lines.append("⚠️ 警告: 无")

    lines.append("=" * 60)
    return "\n".join(lines)
