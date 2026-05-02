#!/usr/bin/env python3
"""
单因子选股策略回测
==================
以「20日动量」为例，演示经典单因子选股 + 分层回测流程。
"""
import sqlite3
import pandas as pd
import numpy as np

DB_PATH = "/root/.astock/astock.db"


def load_data(db_path: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT symbol, date, open, high, low, close, volume, amount, turnover FROM daily ORDER BY symbol, date",
        conn,
        parse_dates=["date"],
    )
    conn.close()
    return df


def compute_momentum(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    df = df.sort_values(["symbol", "date"]).copy()
    df["mom"] = df.groupby("symbol")["close"].pct_change(window)
    df["next_ret"] = df.groupby("symbol")["close"].shift(-1) / df["close"] - 1
    return df


def backtest_single_factor(df: pd.DataFrame, factor_col: str = "mom", top_n: int = 10, rebalance_freq: str = "daily"):
    records = []
    dates = sorted(df["date"].unique())
    last_rebalance = None
    current_holdings = []

    for i, date in enumerate(dates):
        day_df = df[df["date"] == date].copy()
        day_df = day_df.dropna(subset=[factor_col, "next_ret"])
        if day_df.empty:
            continue

        need_rebalance = False
        if rebalance_freq == "daily":
            need_rebalance = True
        elif rebalance_freq == "monthly":
            if last_rebalance is None or date.month != last_rebalance.month:
                need_rebalance = True

        if need_rebalance:
            selected = day_df.nlargest(top_n, factor_col)
            current_holdings = selected["symbol"].tolist()
            last_rebalance = date

        hold_df = day_df[day_df["symbol"].isin(current_holdings)]
        if not hold_df.empty:
            daily_ret = hold_df["next_ret"].mean()
            records.append({
                "date": date,
                "hold_count": len(hold_df),
                "daily_ret": daily_ret,
                "selected": ",".join(current_holdings),
            })

    return pd.DataFrame(records)


def backtest_quintiles(df: pd.DataFrame, factor_col: str = "mom", n_groups: int = 5):
    records = []
    for date, g in df.groupby("date"):
        g = g.dropna(subset=[factor_col, "next_ret"])
        if len(g) < n_groups * 2:
            continue
        g["group"] = pd.qcut(g[factor_col], n_groups, labels=False, duplicates="drop")
        for grp, sub in g.groupby("group"):
            records.append({
                "date": date,
                "group": int(grp),
                "avg_ret": sub["next_ret"].mean(),
                "count": len(sub),
            })
    return pd.DataFrame(records)


def performance_summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    rets = df["daily_ret"].fillna(0)
    cumulative = (1 + rets).cumprod()
    max_dd = (cumulative / cumulative.cummax() - 1).min()
    return {
        "总交易日": len(rets),
        "累计收益": f"{(cumulative.iloc[-1] - 1):.2%}",
        "日均收益": f"{rets.mean():.3%}",
        "年化收益(简易)": f"{rets.mean() * 252:.2%}",
        "胜率": f"{(rets > 0).sum() / len(rets):.1%}",
        "最大回撤": f"{max_dd:.2%}",
        "波动率(年化)": f"{rets.std() * np.sqrt(252):.2%}",
        "夏普(简易)": f"{(rets.mean() / (rets.std() + 1e-8)) * np.sqrt(252):.2f}",
    }


def main():
    print("=" * 60)
    print("单因子选股策略回测 —— 20日动量因子")
    print("=" * 60)

    print("\n[1/4] 加载数据...")
    df = load_data(DB_PATH)
    print(f"数据范围: {df['date'].min().date()} ~ {df['date'].max().date()}")
    print(f"股票数: {df['symbol'].nunique()}, 总记录: {len(df)}")

    print("\n[2/4] 计算因子...")
    df = compute_momentum(df, window=20)
    df_valid = df.dropna(subset=["mom", "next_ret"]).copy()
    print(f"有效记录: {len(df_valid)}")

    print("\n[3/4] 分层回测（Top10 等权每日调仓）...")
    bt = backtest_single_factor(df_valid, factor_col="mom", top_n=10, rebalance_freq="daily")
    if bt.empty:
        print("回测无结果，请检查数据完整性。")
        return

    summary = performance_summary(bt)
    for k, v in summary.items():
        print(f"  {k}: {v}")

    print("\n[4/4] 五分层测试（验证因子单调性）...")
    quin = backtest_quintiles(df_valid, factor_col="mom", n_groups=5)
    if not quin.empty:
        quintile_summary = quin.groupby("group")["avg_ret"].agg(["mean", "std", "count"])
        print(quintile_summary.to_string())
        long_ret = quin[quin["group"] == quin["group"].max()].groupby("date")["avg_ret"].mean()
        short_ret = quin[quin["group"] == quin["group"].min()].groupby("date")["avg_ret"].mean()
        long_short = (long_ret - short_ret).mean()
        print(f"\n多空收益(最高组 - 最低组 日均): {long_short:.3%}")

    print("\n[附] 最近5个交易日持仓:")
    recent = bt.tail(5)[["date", "hold_count", "daily_ret", "selected"]].copy()
    recent["date"] = recent["date"].dt.strftime("%Y-%m-%d")
    recent["daily_ret"] = recent["daily_ret"].apply(lambda x: f"{x:+.2%}")
    print(recent.to_string(index=False))

    print("\nDone.")


if __name__ == "__main__":
    main()
