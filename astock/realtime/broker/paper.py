"""
模拟券商 (Paper Broker)
=======================
用于本地模拟盘验证策略，不产生真实委托。

特性：
- 基于 Tick 价格模拟撮合（限价单按委托价或更优价成交）
- 自动扣除手续费（默认万 3，最低 5 元）
- 资金、持仓本地 JSON 持久化（复用 Portfolio 路径）
- 支持 T+1（当日买入不可卖出）
"""
import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

from astock.realtime.broker.base import (
    BaseBroker, Order, OrderSide, OrderStatus, Trade, AccountSnapshot,
)


# 默认手续费率
COMMISSION_RATE = 0.0003
MIN_COMMISSION = 5.0
STAMP_TAX_RATE = 0.001  # 印花税（卖出时）


class PaperBroker(BaseBroker):
    """模拟盘券商"""

    name = "paper"

    def __init__(self, db_path: Optional[str] = None, initial_cash: float = 100000.0):
        super().__init__()
        self.db_path = Path(db_path or (Path.home() / ".astock" / "paper_broker.json"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # 运行时状态
        self.cash: float = initial_cash
        self.initial_cash = initial_cash
        self.positions: Dict[str, Dict[str, Any]] = {}   # {symbol: {...}}
        self.orders: Dict[str, Order] = {}               # {order_id: Order}
        self.order_history: List[Dict] = []              # 历史订单记录
        self.today_bought: set = set()                   # 今日已买标的（T+1）
        self.trade_date: str = datetime.now().strftime("%Y%m%d")

        self._load()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _load(self):
        if self.db_path.exists():
            with open(self.db_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
                self.cash = raw.get("cash", self.initial_cash)
                self.positions = raw.get("positions", {})
                self.order_history = raw.get("order_history", [])
                # 恢复今日已买标记
                for oh in self.order_history:
                    if oh.get("trade_date") == self.trade_date and oh.get("side") == "买入":
                        self.today_bought.add(oh.get("symbol", ""))

    def _save(self):
        payload = {
            "cash": self.cash,
            "positions": self.positions,
            "order_history": self.order_history[-500:],  # 只保留最近 500 条
            "updated_at": datetime.now().isoformat(),
        }
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # BaseBroker 接口实现
    # ------------------------------------------------------------------
    async def connect(self) -> bool:
        self._connected = True
        print(f"[PaperBroker] 模拟盘已连接，初始资金: {self.cash:.2f}")
        return True

    async def disconnect(self):
        self._connected = False
        self._save()
        print("[PaperBroker] 模拟盘已断开，状态已保存")

    async def send_order(self, order: Order) -> bool:
        """接收订单，状态置为 SUBMITTED，等待 tick 撮合"""
        if not self._connected:
            return False

        order.id = order.id or str(uuid.uuid4())[:12]
        order.status = OrderStatus.SUBMITTED
        order.create_time = datetime.now()
        self.orders[order.id] = order

        # 买入冻结资金
        if order.side == OrderSide.BUY:
            required = order.price * order.volume
            if required > self.cash:
                order.status = OrderStatus.REJECTED
                order.reason = f"资金不足: 需要 {required:.2f}, 可用 {self.cash:.2f}"
                self._on_order_update(order)
                return False
            # 简单冻结（实际应从可用资金扣除）

        # T+1 检查：当日买入不可卖出
        if order.side == OrderSide.SELL and order.symbol in self.today_bought:
            order.status = OrderStatus.REJECTED
            order.reason = "T+1 限制，当日买入不可卖出"
            self._on_order_update(order)
            return False

        self._on_order_update(order)
        return True

    async def cancel_order(self, order_id: str) -> bool:
        order = self.orders.get(order_id)
        if not order or order.is_done:
            return False
        order.status = OrderStatus.CANCELLED
        order.update_time = datetime.now()
        self._on_order_update(order)
        self._save()
        return True

    async def query_orders(self, status: Optional[OrderStatus] = None) -> List[Order]:
        if status is None:
            return list(self.orders.values())
        return [o for o in self.orders.values() if o.status == status]

    async def query_positions(self) -> Dict[str, Dict[str, Any]]:
        return self.positions

    async def query_cash(self) -> AccountSnapshot:
        mv = sum(p.get("market_value", 0) for p in self.positions.values())
        return AccountSnapshot(
            cash=self.cash,
            total_asset=self.cash + mv,
            market_value=mv,
            available_cash=self.cash,
            update_time=datetime.now(),
        )

    # ------------------------------------------------------------------
    # 撮合逻辑（由外部 Loop 在收到 Tick 后调用）
    # ------------------------------------------------------------------
    def on_tick(self, symbol: str, price: float, timestamp: Optional[datetime] = None):
        """
        根据最新价格撮合活跃订单

        Parameters
        ----------
        symbol : str
            标的代码
        price : float
            最新成交价（或买一/卖一中间价）
        """
        ts = timestamp or datetime.now()
        for order in list(self.orders.values()):
            if order.symbol != symbol or order.is_done:
                continue

            matched = False
            if order.side == OrderSide.BUY and price <= order.price:
                # 限价买入：实际成交价取 min(委托价, 最新价)
                fill_price = min(order.price, price)
                matched = True
            elif order.side == OrderSide.SELL and price >= order.price:
                # 限价卖出：实际成交价取 max(委托价, 最新价)
                fill_price = max(order.price, price)
                matched = True

            if matched:
                self._fill_order(order, fill_price, order.volume, ts)

    def _fill_order(self, order: Order, fill_price: float, fill_volume: int, ts: datetime):
        """执行成交"""
        amount = fill_price * fill_volume

        # 计算费用
        commission = max(amount * COMMISSION_RATE, MIN_COMMISSION)
        stamp_tax = amount * STAMP_TAX_RATE if order.side == OrderSide.SELL else 0.0
        total_cost = amount + commission + stamp_tax

        if order.side == OrderSide.BUY:
            if total_cost > self.cash:
                order.status = OrderStatus.REJECTED
                order.reason = f"资金不足: 需要 {total_cost:.2f}, 可用 {self.cash:.2f}"
                self._on_order_update(order)
                return
            self.cash -= total_cost
            # 更新持仓
            pos = self.positions.get(order.symbol, {})
            old_vol = pos.get("volume", 0)
            old_cost = pos.get("cost_price", 0.0) * old_vol
            new_vol = old_vol + fill_volume
            new_cost = (old_cost + amount) / new_vol if new_vol > 0 else 0
            self.positions[order.symbol] = {
                "symbol": order.symbol,
                "name": order.name,
                "volume": new_vol,
                "available": pos.get("available", 0),  # T+1，当日买入不可用
                "cost_price": round(new_cost, 3),
                "market_value": round(new_vol * fill_price, 2),
            }
            self.today_bought.add(order.symbol)
        else:
            # SELL
            pos = self.positions.get(order.symbol)
            if not pos or pos.get("volume", 0) < fill_volume:
                order.status = OrderStatus.REJECTED
                order.reason = "持仓不足"
                self._on_order_update(order)
                return
            self.cash += (amount - commission - stamp_tax)
            pos["volume"] -= fill_volume
            pos["available"] -= fill_volume
            pos["market_value"] = round(pos["volume"] * fill_price, 2)
            if pos["volume"] <= 0:
                del self.positions[order.symbol]

        # 更新订单状态
        order.filled_volume = fill_volume
        order.avg_fill_price = fill_price
        order.status = OrderStatus.FILLED
        order.update_time = ts
        self._on_order_update(order)

        # 生成成交回报
        trade = Trade(
            trade_id=str(uuid.uuid4())[:12],
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            price=fill_price,
            volume=fill_volume,
            time=ts,
        )
        self._on_trade(trade)

        # 记录历史
        self.order_history.append({
            "trade_date": self.trade_date,
            **order.to_dict(),
        })
        self._save()

    def update_position_price(self, symbol: str, price: float):
        """更新持仓市值（由 Loop 定时调用）"""
        if symbol in self.positions:
            pos = self.positions[symbol]
            pos["market_value"] = round(pos["volume"] * price, 2)
