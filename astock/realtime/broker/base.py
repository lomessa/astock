"""
券商交易接口抽象层 (Broker Gateway)
===================================
定义统一的订单、成交、持仓接口，屏蔽不同券商（QMT/Ptrade/通达信）的差异。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Callable


class OrderSide(Enum):
    BUY = "买入"
    SELL = "卖出"


class OrderType(Enum):
    LIMIT = "限价"
    MARKET = "市价"


class OrderStatus(Enum):
    """订单生命周期"""
    PENDING = "待报"          # 系统内部创建，尚未提交券商
    SUBMITTED = "已报"        # 已提交券商
    PARTIAL = "部成"          # 部分成交
    FILLED = "全成"           # 全部成交
    CANCELLED = "已撤"        # 已撤单
    REJECTED = "废单"         # 被券商/交易所拒绝


@dataclass
class Order:
    """订单对象"""
    id: str                          # 系统内部订单号（UUID）
    symbol: str                      # 股票代码
    name: str = ""                   # 股票名称
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.LIMIT
    price: float = 0.0               # 委托价格
    volume: int = 0                  # 委托数量
    filled_volume: int = 0           # 已成交数量
    avg_fill_price: float = 0.0      # 成交均价
    status: OrderStatus = OrderStatus.PENDING
    strategy: str = ""               # 来源策略
    reason: str = ""                 # 下单理由
    create_time: datetime = field(default_factory=datetime.now)
    update_time: datetime = field(default_factory=datetime.now)
    broker_id: Optional[str] = None  # 券商端订单号（如 QMT 的 entrust_no）
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def remaining(self) -> int:
        """剩余未成交数量"""
        return self.volume - self.filled_volume

    @property
    def is_done(self) -> bool:
        """订单是否已结束（全成/已撤/废单）"""
        return self.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "name": self.name,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "price": self.price,
            "volume": self.volume,
            "filled_volume": self.filled_volume,
            "avg_fill_price": self.avg_fill_price,
            "status": self.status.value,
            "strategy": self.strategy,
            "reason": self.reason,
            "create_time": self.create_time.isoformat(),
            "update_time": self.update_time.isoformat(),
            "broker_id": self.broker_id,
        }


@dataclass
class Trade:
    """成交回报"""
    trade_id: str
    order_id: str                    # 对应系统订单号
    symbol: str
    side: OrderSide
    price: float
    volume: int
    time: datetime = field(default_factory=datetime.now)
    broker_trade_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "price": self.price,
            "volume": self.volume,
            "time": self.time.isoformat(),
        }


@dataclass
class AccountSnapshot:
    """账户快照"""
    cash: float = 0.0
    total_asset: float = 0.0
    market_value: float = 0.0
    frozen_cash: float = 0.0         # 冻结资金（挂单占用）
    available_cash: float = 0.0      # 可用资金
    update_time: datetime = field(default_factory=datetime.now)


class BaseBroker(ABC):
    """
    券商接口抽象基类

    实现类需要处理：
    - 连接/断开交易通道
    - 异步下单、撤单
    - 成交回报回调（通过 event_bus 推送 TRADE 事件）
    - 账户/持仓查询
    """

    name: str = "base"

    def __init__(self):
        self._connected: bool = False
        self._trade_callback: Optional[Callable[[Trade], Any]] = None
        self._order_callback: Optional[Callable[[Order], Any]] = None

    @abstractmethod
    async def connect(self) -> bool:
        """建立交易通道连接"""
        pass

    @abstractmethod
    async def disconnect(self):
        """断开连接"""
        pass

    @abstractmethod
    async def send_order(self, order: Order) -> bool:
        """
        提交订单到券商

        Returns
        -------
        bool
            是否成功提交（不代表成交）
        """
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        pass

    @abstractmethod
    async def query_orders(self, status: Optional[OrderStatus] = None) -> List[Order]:
        """查询订单列表"""
        pass

    @abstractmethod
    async def query_positions(self) -> Dict[str, Dict[str, Any]]:
        """
        查询持仓

        Returns
        -------
        Dict[str, dict]
            {symbol: {"volume": int, "available": int, "cost_price": float, "market_value": float}}
        """
        pass

    @abstractmethod
    async def query_cash(self) -> AccountSnapshot:
        """查询资金"""
        pass

    def set_trade_callback(self, callback: Callable[[Trade], Any]):
        """设置成交回报回调（Broker 内部有成交时调用）"""
        self._trade_callback = callback

    def set_order_callback(self, callback: Callable[[Order], Any]):
        """设置订单状态变更回调"""
        self._order_callback = callback

    def _on_trade(self, trade: Trade):
        """内部调用：成交回报"""
        if self._trade_callback:
            try:
                result = self._trade_callback(trade)
                if hasattr(result, "__await__"):
                    import asyncio
                    asyncio.get_running_loop().create_task(result)
            except Exception as e:
                print(f"[Broker:{self.name}] trade callback error: {e}")

    def _on_order_update(self, order: Order):
        """内部调用：订单状态更新"""
        if self._order_callback:
            try:
                result = self._order_callback(order)
                if hasattr(result, "__await__"):
                    import asyncio
                    asyncio.get_running_loop().create_task(result)
            except Exception as e:
                print(f"[Broker:{self.name}] order callback error: {e}")

    @property
    def is_connected(self) -> bool:
        return self._connected
