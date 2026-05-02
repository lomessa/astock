#!/usr/bin/env python3
"""
多因子 ML 模型训练
==================
基于 GP 挖掘的多个因子，训练一个回归模型预测 T+3 收益。

用法:
    python scripts/train_ml_model.py
    python scripts/train_ml_model.py --model gbdt --top_n 10 --lookback 100
    python scripts/train_ml_model.py --model ridge --top_n 20
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from astock.factor_mining.data_prep import prepare_data_from_db
from astock.factor_mining.feature_engine import load_gp_factors, build_feature_matrix, get_feature_columns
from astock.factor_mining.model_trainer import train_model, save_model_bundle


def main():
    parser = argparse.ArgumentParser(description="多因子 ML 模型训练")
    parser.add_argument("--date", default=None, help="训练截止日期 YYYYMMDD（默认今天）")
    parser.add_argument("--lookback", type=int, default=100, help="回看交易日数")
    parser.add_argument("--forward", type=int, default=3, help="预测未来 N 日收益率（默认 3）")
    parser.add_argument("--top_n", type=int, default=10, help="使用 GP 因子池中 fitness 最高的前 N 个因子")
    parser.add_argument("--min_quality", action="store_true", help="只使用 quality_pass=True 的因子")
    parser.add_argument("--model", type=str, default="gbdt", choices=["gbdt", "rf", "ridge"], help="模型类型")
    parser.add_argument("--factor_path", default=None, help="GP 因子文件路径")
    parser.add_argument("--save_path", default=None, help="模型保存路径")
    parser.add_argument("--test_size", type=float, default=0.2, help="测试集比例")
    args = parser.parse_args()

    trade_date = args.date or datetime.now().strftime("%Y%m%d")
    factor_path = args.factor_path or str(Path.home() / ".astock" / "gp_factors.json")
    save_path = args.save_path or str(Path.home() / ".astock" / "ml_model.pkl")

    print("=" * 60)
    print("多因子 ML 模型训练")
    print("=" * 60)
    print(f"日期: {trade_date} | 回看: {args.lookback}天 | 预测: T+{args.forward}")
    print(f"模型: {args.model} | 使用 Top-{args.top_n} 因子")
    print()

    # 1. 加载 GP 因子
    print("[1/5] 加载 GP 因子...")
    gp_factors = load_gp_factors(factor_path, top_n=args.top_n, min_quality_pass=args.min_quality)
    if not gp_factors:
        print("[ERROR] 未找到可用的 GP 因子，请先运行 mine_gp_factor.py")
        return

    factor_exprs = [f["expr"] for f in gp_factors]
    print(f"  加载 {len(factor_exprs)} 个因子表达式")

    # 2. 准备数据
    print("[2/5] 准备历史数据...")
    data_dict, label_df = prepare_data_from_db(trade_date, lookback_days=args.lookback, forward_period=args.forward)
    if data_dict is None:
        print("[ERROR] 数据准备失败")
        return

    stocks = list(data_dict.values())[0].shape[1]
    dates = list(data_dict.values())[0].shape[0]
    print(f"  股票数: {stocks} | 交易日: {dates}")

    # 3. 构建特征矩阵
    print("[3/5] 构建多因子特征矩阵...")
    feature_df = build_feature_matrix(data_dict, factor_exprs, label_df=label_df)
    if feature_df.empty:
        print("[ERROR] 特征矩阵为空")
        return

    feat_cols = get_feature_columns(feature_df)
    print(f"  特征维度: {len(feat_cols)} | 样本数: {len(feature_df)} | 日期数: {feature_df['date'].nunique()}")

    # 4. 训练模型
    print("[4/5] 训练模型...")
    model, metrics = train_model(
        feature_df,
        model_type=args.model,
        test_size=args.test_size,
    )

    print()
    print("=" * 60)
    print("训练结果")
    print("=" * 60)
    print(f"Train RMSE: {metrics['train_rmse']:.6f}")
    print(f"Test  RMSE: {metrics['test_rmse']:.6f}")
    print(f"Train R2:   {metrics['train_r2']:.4f}")
    print(f"Test  R2:   {metrics['test_r2']:.4f}")
    print(f"Test  IC:   {metrics['ic']:.4f}")
    print(f"Test  IR:   {metrics['ir']:.4f}")
    print(f"IC days:    {metrics['ic_days']}")

    if metrics.get("feature_importance"):
        print()
        print("特征重要性:")
        sorted_imp = sorted(metrics["feature_importance"].items(), key=lambda x: abs(x[1]), reverse=True)
        for col, val in sorted_imp:
            expr = factor_exprs[int(col.replace("f_", ""))] if col.replace("f_", "").isdigit() else ""
            print(f"  {col}: {val:+.4f}  ({expr[:60]}...)")

    # 5. 保存
    print()
    print("[5/5] 保存模型...")
    save_model_bundle(model, factor_exprs, feat_cols, metrics, save_path)
    print("Done.")


if __name__ == "__main__":
    main()
