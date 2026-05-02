"""
AStock - 个人短线打板量化框架
=================================
基于 AKShare + Python，面向手动交易的短线量化系统。

核心模块:
    data      : AKShare 数据获取与本地缓存
    strategy  : 首板/连板/炸板回封/竞价超预期等策略
    signal    : 信号引擎，生成交易计划
    sentiment : 市场情绪监控（涨停数、连板高度、炸板率）
    risk      : 风控与仓位管理
    execution : 持仓跟踪与交易清单
    notify    : 钉钉/飞书/企业微信推送
    cli       : 命令行工具

示例:
    from astock.data.client import DataClient
    from astock.strategy.zt_strategies import FirstBoardStrategy
    from astock.signal.engine import SignalEngine

    data = DataClient()
    strategy = FirstBoardStrategy()
    engine = SignalEngine(data=data, strategies=[strategy])
    plans = engine.generate_plans(trade_date="20260423")
"""

__version__ = "0.1.0"
__author__ = "quant"
