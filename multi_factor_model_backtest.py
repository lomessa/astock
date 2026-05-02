#!/usr/bin/env python3
"""
多因子模型回测（3日持有期）
==========================
支持 Walk-forward 滚动训练 + 3日调仓回测。

用法:
    python multi_factor_model_backtest.py
    python multi_factor_model_backtest.py --model gbdt --top_n 10 --train_window 80
    python multi_factor_model_backtest.py --model ridge --predict_window 1
"""
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from astock.factor_mining.data_prep import prepare_data_from_db
from astock.factor_mining.feature_engine import (
    load_gp_factors,
    build_feature_matrix,
    get_feature_columns,
)
from astock.factor_mining.model_trainer import (
    walk_forward_predict,
    create_model,
)

DB_PATH = str(Path.home() / ".astock" / "astock.db")


def performance_summary(df: pd.DataFrame, ret_col: str = "period_ret", fee: float = 0.001) -> dict:
    """计算回测绩效"""
    if df.empty:
        return {}

    rets = df[ret_col].fillna(0)
    # 扣除手续费后的净收益（已在外层扣除，这里不再重复）
    cumulative = (1 + rets).cumprod()
    max_dd = (cumulative / cumulative.cummax() - 1).min()

    # 年化：假设每年 252 个交易日，每 predict_window 天调仓一次
    n_periods = len(rets)
    periods_per_year = 252 / 3  # 默认 3 天一个周期
    total_ret = cumulative.iloc[-1] - 1
    ann_ret = (1 + total_ret) ** (periods_per_year / n_periods) - 1 if n_periods > 0 and total_ret > -1 else 0
    ann_vol = rets.std() * np.sqrt(periods_per_year)
    sharpe = ann_ret / (ann_vol + 1e-8)

    return {
        "总周期数": n_periods,
        "累计收益": f"{total_ret:.2%}",
        "周期均收益": f"{rets.mean():.3%}",
        "年化收益": f"{ann_ret:.2%}",
        "胜率": f"{(rets > 0).sum() / len(rets):.1%}",
        "最大回撤": f"{max_dd:.2%}",
        "年化波动": f"{ann_vol:.2%}",
        "夏普比率": f"{sharpe:.2f}",
    }


def backtest_rebalance_mode(predict_df: pd.DataFrame, top_n: int = 10, predict_window: int = 3) -> pd.DataFrame:
    """
    每 predict_window 天调仓一次，持有 predict_window 天。
    predict_df 中 actual 列已经是 T+predict_window 收益率。
    """
    predict_df = predict_df.sort_values(["date", "symbol"]).copy()
    dates = sorted(predict_df["date"].unique())

    records = []
    for i in range(0, len(dates), predict_window):
        rebalance_date = dates[i]
        day_df = predict_df[predict_df["date"] == rebalance_date]
        if day_df.empty:
            continue

        selected = day_df.nlargest(top_n, "predicted")
        avg_ret = selected["actual"].mean()
        records.append({
            "date": rebalance_date,
            "hold_count": len(selected),
            "period_ret": avg_ret,
            "selected": ",".join(selected["symbol"].tolist()),
        })

    return pd.DataFrame(records)


def backtest_daily_eval(predict_df: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """
    每日评估模式：每天根据预测选 top_n，收益为当日 T+3 收益。
    用于评估模型日度选股能力（持仓会重叠，不直接用于计算净值）。
    """
    records = []
    for date, day_df in predict_df.groupby("date"):
        selected = day_df.nlargest(top_n, "predicted")
        avg_ret = selected["actual"].mean()
        records.append({
            "date": date,
            "hold_count": len(selected),
            "daily_ret": avg_ret,
        })
    return pd.DataFrame(records)


def compute_ic_series(predict_df: pd.DataFrame) -> pd.DataFrame:
    """计算每日 IC 序列"""
    ics = []
    for date, g in predict_df.groupby("date"):
        if len(g) < 10:
            continue
        ic = g["predicted"].corr(g["actual"])
        if pd.notna(ic):
            ics.append({"date": date, "ic": ic})
    return pd.DataFrame(ics)


def main():
    parser = argparse.ArgumentParser(description="多因子模型 Walk-forward 回测")
    parser.add_argument("--date", default=None, help="回测截止日期 YYYYMMDD（默认今天）")
    parser.add_argument("--lookback", type=int, default=200, help="回看交易日数")
    parser.add_argument("--forward", type=int, default=3, help="预测/持有周期（默认 3 日）")
    parser.add_argument("--top_n", type=int, default=10, help="每期选股数量")
    parser.add_argument("--train_window", type=int, default=60, help="每次训练使用多少交易日")
    parser.add_argument("--predict_window", type=int, default=3, help="每次预测/调仓间隔（默认 3 日）")
    parser.add_argument("--model", type=str, default="gbdt", choices=["gbdt", "rf", "ridge"])
    parser.add_argument("--factor_path", default=None, help="GP 因子文件路径")
    parser.add_argument("--fee", type=float, default=0.001, help="双边手续费（默认 0.1%）")
    parser.add_argument("--mode", type=str, default="rebalance", choices=["rebalance", "daily_eval"])
    args = parser.parse_args()

    trade_date = args.date or datetime.now().strftime("%Y%m%d")
    factor_path = args.factor_path or str(Path.home() / ".astock" / "gp_factors.json")

    print("=" * 60)
    print("多因子模型 Walk-forward 回测")
    print("=" * 60)
    print(f"回测截止: {trade_date} | 回看: {args.lookback}天 | 持有: T+{args.forward}")
    print(f"模型: {args.model} | 训练窗: {args.train_window} | 预测窗: {args.predict_window}")
    print(f"选股: Top-{args.top_n} | 手续费: {args.fee:.2%} | 模式: {args.mode}")
    print()

    # 1. 加载 GP 因子
    print("[1/5] 加载 GP 因子...")
    gp_factors = load_gp_factors(factor_path, top_n=20, min_quality_pass=False)
    if not gp_factors:
        print("[ERROR] 未找到 GP 因子，请先运行 mine_gp_factor.py")
        return

    factor_exprs = [f["expr"] for f in gp_factors]
    print(f"  加载 {len(factor_exprs)} 个因子")

    # 2. 准备数据
    print("[2/5] 准备历史数据...")
    data_dict, label_df = prepare_data_from_db(trade_date, lookback_days=args.lookback, forward_period=args.forward)
    if data_dict is None:
        print("[ERROR] 数据准备失败")
        return
    print(f"  股票数: {list(data_dict.values())[0].shape[1]} | 交易日: {list(data_dict.values())[0].shape[0]}")

    # 3. 构建特征
    print("[3/5] 构建多因子特征...")
    feature_df = build_feature_matrix(data_dict, factor_exprs, label_df=label_df)
    if feature_df.empty:
        print("[ERROR] 特征矩阵为空")
        return
    feat_cols = get_feature_columns(feature_df)
    print(f"  特征维度: {len(feat_cols)} | 总样本: {len(feature_df)} | 日期数: {feature_df['date'].nunique()}")

    # 4. Walk-forward 预测
    print("[4/5] Walk-forward 滚动预测...")
    predict_df = walk_forward_predict(
        feature_df,
        model_type=args.model,
        train_window=args.train_window,
        predict_window=args.predict_window,
    )
    if predict_df.empty:
        print("[ERROR] 预测结果为空，可能历史数据不足")
        return
    print(f"  预测周期数: {predict_df['date'].nunique()}")

    # 5. 回测
    print("[5/5] 回测...")
    if args.mode == "rebalance":
        bt = backtest_rebalance_mode(predict_df, top_n=args.top_n, predict_window=args.predict_window)
        # 扣除手续费
        bt["period_ret"] = bt["period_ret"] - args.fee * 2
        ret_col = "period_ret"
    else:
        bt = backtest_daily_eval(predict_df, top_n=args.top_n)
        bt["daily_ret"] = bt["daily_ret"] - args.fee * 2
        ret_col = "daily_ret"

    if bt.empty:
        print("[ERROR] 回测无结果")
        return

    summary = performance_summary(bt, ret_col=ret_col)
    print()
    print("=" * 60)
    print("回测绩效")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k}: {v}")

    # IC 分析
    ic_df = compute_ic_series(predict_df)
    if not ic_df.empty:
        mean_ic = ic_df["ic"].mean()
        ir = mean_ic / (ic_df["ic"].std() + 1e-8)
        print()
        print("IC 分析:")
        print(f"  日均 IC: {mean_ic:.4f}")
        print(f"  IR:      {ir:.4f}")
        print(f"  IC>0 占比: {(ic_df['ic'] > 0).mean():.1%}")

    # 最近几期持仓
    print()
    print("最近 5 期持仓:")
    recent = bt.tail(5)[["date", "hold_count", ret_col]].copy()
    recent[ret_col] = recent[ret_col].apply(lambda x: f"{x:+.2%}")
    print(recent.to_string(index=False))

    print()
    print("Done.")


if __name__ == "__main__":
    main()
