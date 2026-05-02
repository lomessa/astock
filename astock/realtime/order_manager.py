"""
订单管理器 (Order Manager)
==========================
负责订单生命周期管理、与 Broker 交互、并通过 EventBus 通知其他组件。
"""
import uuid
from typing import Any, Dict, List, Optional, Callable
from datetime import datetime

from astock.realtime.event_bus import EventBus, Event, EventType
from astock.realtime.broker.base import BaseBroker, Order, OrderSide, OrderType, OrderStatus, Trade
from astock.signal.plan import TradePlan, PlanAction


class OrderManager:
    """
    订单管理器

    职责：
    1. 将 TradePlan（策略信号）转换为 Order（交易指令）
    2. 提交到 Broker
    3. 跟踪活跃订单，防止重复下单
    4. 接收成交回报，更新内部状态
    """

    def __init__(self, broker: BaseBroker, event_bus: EventBus):
        self.broker = broker
        self.bus = event_bus
        self.active_orders: Dict[str, Order] = {}      # {order_id: Order}
        self.symbol_orders: Dict[str, List[str]] = {}  # {symbol: [order_id, ...]}
        self._lock: set = set()                        # 正在处理的 symbol，防并发重复

        # 注册 broker 回调
        self.broker.set_trade_callback(self._on_trade)
        self.broker.set_order_callback(self._on_order_update)

    # ------------------------------------------------------------------
    # 信号 → 订单
    # ------------------------------------------------------------------
    async def submit_plan(self, plan: TradePlan, price: Optional[float] = None, volume: Optional[int] = None) -> Optional[Order]:
        """
        根据交易计划提交订单

        Parameters
        ----------
        plan : TradePlan
        price : float, optional
            指定委托价格，默认使用 plan.entry_price

        Returns
        -------
        Order or None
        """
        if plan.action not in (PlanAction.BUY, PlanAction.SELL):
            return None

        symbol = plan.symbol

        # 防重：该标的有活跃订单时拒绝新单（同向也拒绝，简化逻辑）
        if symbol in self._lock:
            print(f"[OrderManager] {symbol} 已有挂单或正在处理，跳过")
            return None

        side = OrderSide.BUY if plan.action == PlanAction.BUY else OrderSide.SELL
        order_price = price if price is not None else (plan.entry_price or 0.0)
        if order_price <= 0:
            print(f"[OrderManager] {symbol} 价格无效: {order_price}")
            return None

        # 计算数量
        vol = volume if volume is not None else self._calc_volume(plan)
        if vol <= 0:
            print(f"[OrderManager] {symbol} 计算数量为 0，跳过")
            return None
        # 如果是买入，按 100 股取整
        if side == OrderSide.BUY:
            vol = int(vol / 100) * 100
            if vol <= 0:
                vol = 100

        order = Order(
            id=str(uuid.uuid4())[:12],
            symbol=symbol,
            name=plan.name,
            side=side,
            order_type=OrderType.LIMIT,
            price=round(order_price, 2),
            volume=vol,
            status=OrderStatus.PENDING,
            strategy=plan.strategy,
            reason=plan.reason,
        )

        self._lock.add(symbol)
        ok = await self.broker.send_order(order)
        if ok:
            self.active_orders[order.id] = order
            self.symbol_orders.setdefault(symbol, []).append(order.id)
            await self.bus.publish(Event(EventType.ORDER, payload=order, source="order_manager"))
        else:
            self._lock.discard(symbol)
            print(f"[OrderManager] {symbol} 下单被拒: {order.reason}")
        return order if ok else None

    async def cancel(self, order_id: str) -> bool:
        """撤单"""
        ok = await self.broker.cancel_order(order_id)
        return ok

    async def cancel_all(self, symbol: Optional[str] = None):
        """撤销全部或某标的的活跃订单"""
        ids = []
        if symbol:
            ids = [oid for oid in self.symbol_orders.get(symbol, [])]
        else:
            ids = [o.id for o in self.active_orders.values() if not o.is_done]
        for oid in ids:
            await self.cancel(oid)

    # ------------------------------------------------------------------
    # Broker 回调
    # ------------------------------------------------------------------
    def _on_order_update(self, order: Order):
        """订单状态更新回调"""
        if order.id in self.active_orders:
            self.active_orders[order.id] = order
        if order.is_done:
            self._lock.discard(order.symbol)
            # 异步发布事件
            try:
                import asyncio
                asyncio.get_running_loop().create_task(
                    self.bus.publish(Event(EventType.ORDER, payload=order, source="order_manager"))
                )
            except RuntimeError:
                pass

    def _on_trade(self, trade: Trade):
        """成交回报回调"""
        order = self.active_orders.get(trade.order_id)
        if order and order.is_done:
            self._lock.discard(order.symbol)

        try:
            import asyncio
            asyncio.get_running_loop().create_task(
                self.bus.publish(Event(EventType.TRADE, payload=trade, source="order_manager"))
            )
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def has_active_order(self, symbol: str) -> bool:
        """标的是否有活跃订单"""
        if symbol in self._lock:
            return True
        for oid in self.symbol_orders.get(symbol, []):
            o = self.active_orders.get(oid)
            if o and not o.is_done:
                return True
        return False

    def get_active_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """获取活跃订单"""
        orders = [o for o in self.active_orders.values() if not o.is_done]
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        return orders

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _calc_volume(self, plan: TradePlan) -> int:
        """
        根据建议仓位计算买入股数（简化版）
        实际生产环境需要查询账户资金、标的价格、最小变动单位
        """
        if plan.action == PlanAction.SELL:
            # 卖出时默认全仓，后续可以从持仓查询
            return 0  # 需要外部传入或查询持仓

        # 买入：先按 100 股整数倍简化
        price = plan.entry_price or 0.0
        if price <= 0:
            return 0
        # 假设总资金 10w，按 suggested_position 计算
        # 这里用一个简化策略：建议仓位是比例，先假设总资产 100000
        # 实际应由 Portfolio/Account 提供总资产
        assumed_asset = 100000.0
        amount = assumed_asset * plan.suggested_position
        volume = int(amount / price / 100) * 100
        return max(volume, 100)
