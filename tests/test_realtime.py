"""
Realtime 模块单元测试
====================
验证事件总线、模拟盘、订单管理、策略适配器等核心组件。
"""
import asyncio
import pytest
import tempfile
from datetime import datetime

from astock.realtime.event_bus import EventBus, Event, EventType
from astock.realtime.broker.paper import PaperBroker
from astock.realtime.broker.base import Order, OrderSide, OrderStatus
from astock.realtime.order_manager import OrderManager
from astock.realtime.strategy_adapter import LegacyStrategyAdapter, Tick, Signal
from astock.realtime.risk_adapter import RealtimeRiskManager
from astock.strategy.zt_strategies import FirstBoardStrategy
from astock.signal.plan import TradePlan, PlanAction


# ------------------------------------------------------------------
# EventBus
# ------------------------------------------------------------------
@pytest.mark.asyncio
async def test_event_bus_publish_subscribe():
    bus = EventBus()
    received = []

    def handler(event: Event):
        received.append(event.type)

    bus.subscribe(EventType.TICK, handler)
    await bus.start()

    await bus.publish(Event(EventType.TICK, payload={"price": 10.0}))
    await asyncio.sleep(0.1)

    assert len(received) == 1
    assert received[0] == EventType.TICK

    await bus.stop()


# ------------------------------------------------------------------
# PaperBroker
# ------------------------------------------------------------------
@pytest.mark.asyncio
async def test_paper_broker_connect():
    broker = PaperBroker(db_path=tempfile.mktemp(suffix=".json"), initial_cash=100000)
    ok = await broker.connect()
    assert ok is True
    assert broker.is_connected
    await broker.disconnect()


@pytest.mark.asyncio
async def test_paper_broker_buy_order():
    broker = PaperBroker(db_path=tempfile.mktemp(suffix=".json"), initial_cash=100000)
    await broker.connect()

    order = Order(
        id="test-001",
        symbol="000001",
        name="平安银行",
        side=OrderSide.BUY,
        price=10.0,
        volume=1000,
    )
    ok = await broker.send_order(order)
    assert ok is True
    assert order.status == OrderStatus.SUBMITTED

    # 用 tick 撮合
    broker.on_tick("000001", 10.0)
    assert order.status == OrderStatus.FILLED
    assert order.filled_volume == 1000

    # 检查持仓
    positions = await broker.query_positions()
    assert "000001" in positions
    assert positions["000001"]["volume"] == 1000

    # 检查资金减少
    cash_info = await broker.query_cash()
    assert cash_info.cash < 100000

    await broker.disconnect()


@pytest.mark.asyncio
async def test_paper_broker_sell_order():
    broker = PaperBroker(db_path=tempfile.mktemp(suffix=".json"), initial_cash=100000)
    await broker.connect()

    # 先买入
    buy_order = Order(id="test-002", symbol="000001", name="平安银行", side=OrderSide.BUY, price=10.0, volume=1000)
    await broker.send_order(buy_order)
    broker.on_tick("000001", 10.0)

    # 模拟次日（清空 T+1 标记）再卖出
    broker.today_bought.clear()
    sell_order = Order(id="test-003", symbol="000001", name="平安银行", side=OrderSide.SELL, price=11.0, volume=1000)
    await broker.send_order(sell_order)
    broker.on_tick("000001", 11.0)

    assert sell_order.status == OrderStatus.FILLED
    positions = await broker.query_positions()
    assert "000001" not in positions  # 已清仓

    await broker.disconnect()


@pytest.mark.asyncio
async def test_paper_broker_t1_restriction():
    broker = PaperBroker(initial_cash=100000)
    await broker.connect()

    buy_order = Order(id="test-004", symbol="000001", name="平安银行", side=OrderSide.BUY, price=10.0, volume=1000)
    await broker.send_order(buy_order)
    broker.on_tick("000001", 10.0)

    # 当日再卖
    sell_order = Order(id="test-005", symbol="000001", name="平安银行", side=OrderSide.SELL, price=11.0, volume=1000)
    ok = await broker.send_order(sell_order)
    assert ok is False  # T+1 限制
    assert sell_order.status == OrderStatus.REJECTED

    await broker.disconnect()


# ------------------------------------------------------------------
# OrderManager
# ------------------------------------------------------------------
@pytest.mark.asyncio
async def test_order_manager_submit_and_cancel():
    bus = EventBus()
    await bus.start()
    broker = PaperBroker(db_path=tempfile.mktemp(suffix=".json"), initial_cash=100000)
    await broker.connect()
    om = OrderManager(broker, bus)

    plan = TradePlan(
        symbol="000001",
        name="平安银行",
        action=PlanAction.BUY,
        strategy="test",
        entry_price=10.0,
        suggested_position=0.1,
    )
    order = await om.submit_plan(plan, price=10.0, volume=1000)
    assert order is not None
    assert order.symbol == "000001"
    assert om.has_active_order("000001")

    # 撮合
    broker.on_tick("000001", 10.0)
    assert not om.has_active_order("000001")  # 已成交

    await broker.disconnect()
    await bus.stop()


# ------------------------------------------------------------------
# LegacyStrategyAdapter
# ------------------------------------------------------------------
def test_legacy_adapter_init():
    # 使用一个不依赖外部数据的简单场景验证初始化
    st = FirstBoardStrategy()
    adapter = LegacyStrategyAdapter(st, trade_date="20990101")
    assert adapter.name == "adapter_首板"
    assert adapter.candidates.empty


def test_legacy_adapter_on_tick_no_candidates():
    st = FirstBoardStrategy()
    adapter = LegacyStrategyAdapter(st, trade_date="20990101")
    tick = Tick(symbol="000001", price=10.0)
    ctx = {}
    sig = adapter.on_tick(ctx, tick)
    assert sig is None


# ------------------------------------------------------------------
# RealtimeRiskManager
# ------------------------------------------------------------------
def test_risk_blacklist():
    rm = RealtimeRiskManager()
    # 临时设置黑名单
    rm.cfg.blacklist = ["000001"]

    sig = Signal(symbol="000001", name="测试", action=PlanAction.BUY, price=10.0, volume=100)
    ok, reason = rm.check_signal(sig, {"total_asset": 100000}, {}, [])
    assert ok is False
    assert "黑名单" in reason


def test_risk_position_limit():
    rm = RealtimeRiskManager()
    rm.cfg.max_holding_count = 2

    sig = Signal(symbol="000003", name="测试", action=PlanAction.BUY, price=10.0, volume=100)
    positions = {
        "000001": {"volume": 100},
        "000002": {"volume": 100},
    }
    ok, reason = rm.check_signal(sig, {"total_asset": 100000}, positions, [])
    assert ok is False
    assert "持仓数量已达上限" in reason


def test_risk_sell_without_position():
    rm = RealtimeRiskManager()
    rm.cfg.blacklist = []  # 清空黑名单避免干扰
    sig = Signal(symbol="000001", name="测试", action=PlanAction.SELL, price=10.0, volume=100)
    ok, reason = rm.check_signal(sig, {"total_asset": 100000}, {}, [])
    assert ok is False
    assert "无持仓" in reason
