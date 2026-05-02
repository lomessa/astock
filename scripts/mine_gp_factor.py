#!/usr/bin/env python3
"""
GP 遗传规划因子挖掘
====================
基于全市场日线数据，自动挖掘新因子并追加到本地 gp_factors.json

用法:
    python scripts/mine_gp_factor.py           # 默认参数
    python scripts/mine_gp_factor.py --gen 30  # 增加进化代数
    python scripts/mine_gp_factor.py --pop 200 # 增加种群大小
"""
import argparse
import json
import sys
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from deap import gp

# 把项目根目录加入路径
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from astock.factor_mining.data_prep import prepare_data_from_db
from astock.factor_mining.gp_engine import FactorGPEngine
from astock.factor_mining.fitness import evaluate_ic
from astock.factor_mining.factor_quality import evaluate_factor_quality, format_quality_report

warnings.filterwarnings("ignore")


def save_factor(expr_str: str, mean_ic: float, ir: float, fitness: float,
                direction: str, stocks: int, factor_path: str, quality_report: dict = None):
    """将新因子追加到 gp_factors.json"""
    try:
        with open(factor_path, "r") as f:
            factors = json.load(f)
    except Exception:
        factors = []

    entry = {
        "expr": expr_str,
        "ic": round(mean_ic, 4),
        "ir": round(ir, 4),
        "fitness": round(fitness, 4),
        "direction": direction,
        "date": datetime.now().strftime("%Y%m%d"),
        "stocks": stocks,
    }
    if quality_report:
        entry["quality_pass"] = quality_report.get("pass", False)
        entry["quality_ic_ir"] = quality_report.get("ic", {}).get("ir")
        entry["quality_monotonicity"] = quality_report.get("group", {}).get("monotonicity")

    factors.append(entry)

    with open(factor_path, "w") as f:
        json.dump(factors, f, indent=2, ensure_ascii=False)

    print(f"\n💾 因子已保存到 {factor_path}（共 {len(factors)} 条）")


def main():
    parser = argparse.ArgumentParser(description="GP 遗传规划因子挖掘")
    parser.add_argument("--date", default=None, help="截止日期 YYYYMMDD（默认今天）")
    parser.add_argument("--lookback", type=int, default=50, help="回看交易日数")
    parser.add_argument("--forward", type=int, default=3, help="预测未来 N 日收益率（默认 3）")
    parser.add_argument("--pop", type=int, default=100, help="种群大小")
    parser.add_argument("--gen", type=int, default=20, help="进化代数")
    parser.add_argument("--cxpb", type=float, default=0.5, help="交叉概率")
    parser.add_argument("--mutpb", type=float, default=0.2, help="变异概率")
    parser.add_argument("--depth", type=int, default=5, help="树最大深度")
    parser.add_argument("--factor-path", default=None, help="因子保存路径")
    args = parser.parse_args()

    trade_date = args.date or datetime.now().strftime("%Y%m%d")
    factor_path = args.factor_path or str(Path.home() / ".astock" / "gp_factors.json")

    print("=" * 60)
    print("GP 遗传规划因子挖掘")
    print("=" * 60)
    print(f"日期: {trade_date} | 回看: {args.lookback}天 | 预测: T+{args.forward}")
    print(f"种群: {args.pop} | 代数: {args.gen} | 深度: {args.depth}")
    print()

    # 1. 准备数据
    print("[1/5] 准备数据...")
    data_dict, label_df = prepare_data_from_db(trade_date, lookback_days=args.lookback, forward_period=args.forward)
    if data_dict is None:
        print("[ERROR] 数据准备失败，请检查本地数据库")
        return

    stocks = list(data_dict.values())[0].shape[1]
    dates = list(data_dict.values())[0].shape[0]
    print(f"  股票数: {stocks} | 交易日: {dates}")

    # 2. 初始化引擎
    print("[2/5] 初始化 GP 引擎...")
    engine = FactorGPEngine(
        data_dict=data_dict,
        label_df=label_df,
        pop_size=args.pop,
        generations=args.gen,
        cxpb=args.cxpb,
        mutpb=args.mutpb,
        max_depth=args.depth,
    )

    # 3. 运行进化
    print("[3/5] 开始进化...")
    pop, log, hof = engine.run(verbose=True)

    # 4. 评估最优因子
    print("[4/5] 评估最优因子...")
    best = hof[0]
    expr_str = str(best)

    try:
        factor = gp.compile(expr=best, pset=engine.pset)
    except Exception as e:
        print(f"[ERROR] 编译失败: {e}")
        return

    if not isinstance(factor, pd.DataFrame) or factor.empty:
        print("[ERROR] 最优因子返回空结果")
        return

    mean_ic, ir, ics = evaluate_ic(factor, label_df)
    fitness = abs(mean_ic) * min(abs(ir), 2.0)
    direction = "reverse" if mean_ic < 0 else "forward"

    print()
    print("=" * 60)
    print("🏆 最优因子")
    print("=" * 60)
    print(f"表达式: {expr_str}")
    print(f"IC:     {mean_ic:.4f}")
    print(f"IR:     {ir:.4f}")
    print(f"Fitness:{fitness:.4f}")
    print(f"方向:   {direction}")
    print(f"覆盖:   {stocks} 只股票")
    print("=" * 60)

    # 5. 质量评估
    print("\n[5/5] 因子质量体检...")
    quality = evaluate_factor_quality(factor, label_df)
    print(format_quality_report(quality))

    # 保存
    save_factor(expr_str, mean_ic, ir, fitness, direction, stocks, factor_path, quality)

    # Top 5 Hall of Fame
    print("\n📊 Hall of Fame (Top 5):")
    print("-" * 60)
    for i, ind in enumerate(hof[:5], 1):
        ic, ir_val, _ = evaluate_ic(gp.compile(expr=ind, pset=engine.pset), label_df)
        fit = abs(ic) * min(abs(ir_val), 2.0)
        print(f"{i}. {ind}")
        print(f"   IC={ic:.4f} IR={ir_val:.4f} fitness={fit:.4f}")
        print()

    print("Done.")


if __name__ == "__main__":
    main()
