#!/usr/bin/env python3
"""
AStock 启动入口
==============
快速启动示例，也可以直接使用 CLI：
    python -m astock.cli.main scan
"""
from datetime import datetime

from astock.data.client import DataClient
from astock.strategy.zt_strategies import (
    FirstBoardStrategy,
    ContinuousBoardStrategy,
    ResealStrategy,
    AuctionStrategy,
)
from astock.strategy.sector_flow_strategy import (
    SectorFlowLeaderStrategy,
    SectorFlowSurgeStrategy,
)
from astock.signal.engine import SignalEngine
from astock.sentiment.market import SentimentAnalyzer
from astock.execution.portfolio import Portfolio
from astock.notify.notifier import Notifier


def main():
    print("=" * 50)
    print("AStock - 个人短线打板量化框架")
    print("=" * 50)

    # 1. 数据客户端（多源聚合，自动降级）
    data = DataClient()

    # 2. 市场情绪（决定今天做不做）
    print("\n[1/4] 分析市场情绪...")
    analyzer = SentimentAnalyzer(data)
    sentiment = analyzer.analyze()
    print(analyzer.format_report(sentiment))

    if sentiment["score"] < 30:
        print("\n⚠️ 情绪过冷，建议空仓休息，跳过选股。")
        return

    # 3. 策略选股
    print("\n[2/4] 策略选股...")
    strategies = [
        FirstBoardStrategy(),
        ContinuousBoardStrategy(),
        ResealStrategy(),
        AuctionStrategy(),
        SectorFlowLeaderStrategy(),
        SectorFlowSurgeStrategy(),
    ]
    engine = SignalEngine(data=data, strategies=strategies)
    plans = engine.generate_plans()

    if not plans:
        print("今日无交易信号。")
        return

    print(engine.format_plans(plans))

    # 4. 持仓与预警
    print("\n[3/4] 持仓检查...")
    pf = Portfolio(data)
    pf.print_summary()

    # 5. 推送（可选）
    print("\n[4/4] 推送通知...")
    notifier = Notifier()
    title = f"AStock 盘前计划 [{datetime.now().strftime('%m-%d')}]"
    content = engine.format_plans(plans)
    ok = notifier.send(title, content)
    if ok:
        print("推送成功 ✅")
    else:
        print("推送 skipped（未配置 Webhook 或信号不足）")

    print("\nDone.")


if __name__ == "__main__":
    main()
