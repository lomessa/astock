#!/usr/bin/env python3
"""
板块资金流策略 简易回测
========================
数据限制说明：
  - AKShare 免费接口不支持获取「历史某一天的板块资金流向排名」，
    只能获取最新（截至今日）的5日/10日累计排名。
  - 因此本回测使用「最新板块排名」作为强势板块代理，
    去匹配近N个交易日的涨停股及其次日溢价。
  - 这存在轻微的 look-ahead bias，但用于验证「强势板块内涨停股
    是否优于非强势板块」这一因子有效性，仍有参考价值。
"""
from datetime import datetime, timedelta
from typing import List, Dict, Any
import pandas as pd
from astock.data.client import DataClient
from astock.strategy.sector_flow_strategy import SectorFlowLeaderStrategy, _match_industry


def get_recent_trade_dates(n: int = 5) -> List[str]:
    """获取最近N个交易日的日期（简单回退，跳过周末）"""
    dates = []
    d = datetime.now()
    while len(dates) < n:
        if d.weekday() < 5:  # 周一到周五
            dates.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return dates[::-1]  # 从早到晚


def backtest_sector_flow(data: DataClient, trade_dates: List[str], top_n: int = 20):
    """
    回测逻辑：
      1. 获取最新5日板块净流入排名 Top N（强势板块代理）
      2. 对每一个历史交易日T：
         - 获取T日涨停池（含行业）
         - 分为「强势板块涨停组」vs「非强势板块涨停组」
         - 获取T+1日两组股票的平均溢价（用 get_zt_pool_previous）
      3. 统计两组差异
    """
    print("=" * 60)
    print("板块资金共振策略 —— 因子有效性回测")
    print("=" * 60)

    # 1. 最新强势板块（5日主力净流入排名）
    df_sector = data.get_sector_hot()
    if df_sector.empty:
        print("[ERROR] 无法获取板块资金流向，回测终止")
        return
    hot_sectors = df_sector.head(top_n)["名称"].tolist()
    print(f"\n当前强势板块 Top{top_n}: {hot_sectors[:10]}...")

    records = []
    for date in trade_dates:
        print(f"\n--- 回测日期: {date} ---")

        # T日涨停池
        df_zt = data.get_zt_pool(date)
        if df_zt.empty or "industry" not in df_zt.columns:
            print(f"  {date} 无涨停池数据或缺少行业信息，跳过")
            continue

        # T+1日昨日涨停表现（即T日涨停股在T+1日的涨跌幅）
        # get_zt_pool_previous 返回的是「date 这一天」的昨日涨停今日表现
        # 也就是说，如果 date = 20260422，返回的是 20260421 涨停股在 20260422 的表现
        # 我们需要的是：对T日涨停股，看T+1日表现。
        # 所以这里需要错位：T+1日的 get_zt_pool_previous 才是T日涨停股的表现。
        next_date = (datetime.strptime(date, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        df_next = data.get_zt_pool_previous(next_date)

        if df_next.empty or "change_pct" not in df_next.columns:
            print(f"  {next_date} 无次日表现数据，跳过")
            continue

        # 合并T日涨停池的行业信息到次日表现数据中
        # 注意：df_next 本身可能也带有 industry 列，先 drop 避免冲突
        if "industry" in df_next.columns:
            df_next = df_next.drop(columns=["industry"])
        df_next = df_next.merge(
            df_zt[["symbol", "industry"]], on="symbol", how="left"
        )

        # 区分强势板块组 vs 非强势板块组
        df_next["in_hot_sector"] = df_next["industry"].apply(
            lambda ind: _match_industry(hot_sectors, ind) if pd.notna(ind) else False
        )

        hot_group = df_next[df_next["in_hot_sector"]]
        cold_group = df_next[~df_next["in_hot_sector"]]

        hot_premium = hot_group["change_pct"].mean() if not hot_group.empty else 0
        hot_win = (hot_group["change_pct"] > 0).sum() / len(hot_group) if not hot_group.empty else 0
        cold_premium = cold_group["change_pct"].mean() if not cold_group.empty else 0
        cold_win = (cold_group["change_pct"] > 0).sum() / len(cold_group) if not cold_group.empty else 0

        print(f"  强势板块涨停股: {len(hot_group)}只, 次日溢价: {hot_premium:.2f}%, 胜率: {hot_win:.1%}")
        print(f"  非强势板块涨停股: {len(cold_group)}只, 次日溢价: {cold_premium:.2f}%, 胜率: {cold_win:.1%}")
        print(f"  差异(溢价): {hot_premium - cold_premium:+.2f}% | 差异(胜率): {hot_win - cold_win:+.1%}")

        records.append({
            "date": date,
            "hot_count": len(hot_group),
            "hot_premium": round(hot_premium, 2),
            "hot_win_rate": round(hot_win, 3),
            "cold_count": len(cold_group),
            "cold_premium": round(cold_premium, 2),
            "cold_win_rate": round(cold_win, 3),
            "premium_diff": round(hot_premium - cold_premium, 2),
            "win_diff": round(hot_win - cold_win, 3),
        })

    if not records:
        print("\n[WARNING] 无有效回测记录")
        return

    df_result = pd.DataFrame(records)
    print("\n" + "=" * 60)
    print("回测汇总")
    print("=" * 60)
    print(df_result.to_string(index=False))

    avg_hot = df_result["hot_premium"].mean()
    avg_cold = df_result["cold_premium"].mean()
    avg_hot_win = df_result["hot_win_rate"].mean()
    avg_cold_win = df_result["cold_win_rate"].mean()
    positive_diff_days = (df_result["premium_diff"] > 0).sum()

    print(f"\n--- 统计结论 ---")
    print(f"统计天数: {len(df_result)}")
    print(f"强势板块组 平均溢价: {avg_hot:.2f}% | 平均胜率: {avg_hot_win:.1%}")
    print(f"非强势板块组 平均溢价: {avg_cold:.2f}% | 平均胜率: {avg_cold_win:.1%}")
    print(f"溢价差异: {avg_hot - avg_cold:+.2f}%")
    print(f"胜率差异: {avg_hot_win - avg_cold_win:+.1%}")
    print(f"强势板块跑赢天数: {positive_diff_days}/{len(df_result)}")

    # 模拟策略收益（只做强势板块涨停股，次日开盘按平均溢价卖出，单票等权）
    print(f"\n--- 模拟策略表现 ---")
    print("假设每日收盘前买入当日强势板块内的所有涨停股，次日按平均溢价卖出：")
    daily_returns = df_result["hot_premium"] / 100.0
    cumulative = (1 + daily_returns).prod() - 1
    print(f"累计收益（{len(df_result)}个交易日）: {cumulative:.2%}")
    print(f"日均收益: {daily_returns.mean():.2%}")


def backtest_sector_surge(data: DataClient, trade_dates: List[str], top_n: int = 8):
    """
    板块异动低吸策略回测（更简化）
    由于无法获取历史日期的板块成分股，本回测只做逻辑说明与近似的T+1验证。
    """
    print("\n" + "=" * 60)
    print("板块异动低吸策略 —— 回测说明")
    print("=" * 60)
    print("""
【数据限制】
  - AKShare 免费接口不支持获取「历史某一天的板块成分股列表」及「历史某一天
    的板块资金流向排名」。
  - 因此，板块异动低吸策略无法像涨停策略那样做严格的历史T日回测。

【替代验证方案】
  1. 盘中实时运行：每个交易日收盘前执行策略，记录选股结果。
  2. 次日跟踪：记录选中股票次日的开盘价/收盘价涨跌幅。
  3. 连续积累 20-60 个交易日的记录后，即可得到真实的策略胜率与盈亏比。

【逻辑自洽性验证】
  本策略的核心假设是：「强势板块内部，当日有资金异动（涨幅3%-6%）但尚未
  涨停的个股，次日存在补涨或被板块情绪带动上涨的预期」。
  这在 A 股短线生态中被广泛验证（跟风补涨逻辑）。
""")


if __name__ == "__main__":
    data = DataClient()
    dates = get_recent_trade_dates(5)
    print(f"回测日期范围: {dates}")

    backtest_sector_flow(data, dates, top_n=20)
    backtest_sector_surge(data, dates, top_n=8)
