"""
事件总线 (Event Bus)
====================
基于 asyncio 的发布-订阅事件总线，是实时交易系统的核心通信机制。

事件流：
    Tick/Bar → Strategy → Signal → Risk → OrderManager → Broker → Trade → Portfolio
"""
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set


class EventType(Enum):
    """事件类型"""
    # 行情事件
    TICK = auto()           # 逐笔行情
    BAR = auto()            # K线（分钟/日）
    MARKET_STATE = auto()   # 市场状态变化（开盘、收盘、竞价）

    # 策略事件
    SIGNAL = auto()         # 策略信号（买入/卖出）

    # 订单事件
    ORDER = auto()          # 订单状态变化
    TRADE = auto()          # 成交回报

    # 系统事件
    TIMER = auto()          # 定时器（用于定时任务）
    ERROR = auto()          # 错误/异常
    SHUTDOWN = auto()       # 系统关闭


@dataclass
class Event:
    """事件对象"""
    type: EventType
    payload: Any = None
    source: str = ""
    timestamp: datetime = field(default_factory=datetime.now)

    def __repr__(self) -> str:
        return f"Event({self.type.name}, src={self.source}, ts={self.timestamp.isoformat()})"


class EventBus:
    """
    异步事件总线

    Usage:
        bus = EventBus()

        # 订阅
        bus.subscribe(EventType.TICK, my_strategy.on_tick)
        bus.subscribe(EventType.TRADE, my_portfolio.on_trade)

        # 发布
        await bus.publish(Event(EventType.TICK, tick_data, source="broker"))

        # 启动消费循环
        await bus.start()
    """

    def __init__(self, queue_size: int = 10000):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self._handlers: Dict[EventType, List[Callable]] = {et: [] for et in EventType}
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._all_handlers: List[Callable] = []  # 监听所有事件的 handler

    def subscribe(self, event_type: EventType, handler: Callable[[Event], Any]):
        """订阅特定类型事件"""
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: EventType, handler: Callable[[Event], Any]):
        """取消订阅"""
        if handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)

    def subscribe_all(self, handler: Callable[[Event], Any]):
        """订阅所有事件（用于日志、监控）"""
        if handler not in self._all_handlers:
            self._all_handlers.append(handler)

    async def publish(self, event: Event):
        """发布事件到队列"""
        if self._queue.full():
            # 队列满时丢弃最旧的事件（通常是行情）
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        await self._queue.put(event)

    def publish_sync(self, event: Event):
        """同步发布（在非协程环境使用）"""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.publish(event))
        except RuntimeError:
            # 无事件循环时静默丢弃（应避免在异步外调用）
            pass

    async def start(self):
        """启动事件分发循环"""
        self._running = True
        self._task = asyncio.create_task(self._dispatch_loop())

    async def stop(self):
        """停止事件分发"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _dispatch_loop(self):
        """事件分发主循环"""
        while self._running:
            try:
                event: Event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            # 分发到所有订阅者
            handlers = self._handlers[event.type] + self._all_handlers
            for handler in handlers:
                try:
                    result = handler(event)
                    if asyncio.iscoroutine(result):
                        asyncio.create_task(result)
                except Exception as e:
                    # handler 异常不应阻塞总线
                    err_event = Event(
                        EventType.ERROR,
                        payload={"exception": e, "original_event": event, "handler": handler},
                        source="event_bus",
                    )
                    # 同步发布错误，避免递归
                    for h in self._handlers[EventType.ERROR]:
                        try:
                            h(err_event)
                        except Exception:
                            pass

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()
