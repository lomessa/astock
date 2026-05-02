"""
实时交易主循环 (Trading Loop)
==============================
 orchestrate 整个实时交易系统：
    行情轮询 → EventBus → 策略 → 风控 → 订单 → Broker → 成交 → Portfolio

由于当前无 WebSocket 行情源，采用"定时轮询 DataClient"的模拟模式，
便于在无真实券商环境下调试验证策略逻辑。
"""
import asyncio
import sys
from enum import Enum, auto
from datetime import datetime, time
from typing import Any, Dict, List, Optional

import pandas as pd

from astock.data import DataClient
from astock.realtime.event_bus import EventBus, Event, EventType
from astock.realtime.broker.paper import PaperBroker
from astock.realtime.order_manager import OrderManager
from astock.realtime.risk_adapter import RealtimeRiskManager
from astock.realtime.strategy_adapter import (
    RealtimeStrategy, LegacyStrategyAdapter, Tick, Signal,
)
from astock.signal.plan import PlanAction
from astock.notify.notifier import Notifier


class MarketState(Enum):
    """市场状态机"""
    IDLE = auto()         # 未启动
    PRE_MARKET = auto()   # 盘前（09:00-09:25）
    AUCTION = auto()      # 集合竞价（09:25-09:30）
    OPEN = auto()         # 开盘（09:30）
    INTRADAY = auto()     # 盘中（09:30-14:57）
    CLOSE = auto()        # 收盘（15:00）
    POST_MARKET = auto()  # 盘后


class TradingLoop:
    """
    实时交易主循环

    Parameters
    ----------
    strategies : List[RealtimeStrategy]
        实时策略列表（可用 LegacyStrategyAdapter 包装旧策略）
    broker : PaperBroker
        券商接口（默认模拟盘）
    data : DataClient, optional
        数据客户端
    poll_interval : float
        行情轮询间隔（秒），默认 5 秒
    """

    def __init__(
        self,
        strategies: List[RealtimeStrategy],
        broker: Optional[PaperBroker] = None,
        data: Optional[DataClient] = None,
        poll_interval: float = 5.0,
    ):
        self.strategies = strategies
        self.broker = broker or PaperBroker()
        self.data = data or DataClient()
        self.poll_interval = poll_interval

        # 核心组件
        self.bus = EventBus()
        self.order_manager = OrderManager(self.broker, self.bus)
        self.risk = RealtimeRiskManager()
        self.notifier = Notifier()

        # 状态
        self.state = MarketState.IDLE
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._trade_date = datetime.now().strftime("%Y%m%d")

        # 行情缓存
        self._last_snapshot: pd.DataFrame = pd.DataFrame()
        self._watch_symbols: set = set()   # 需要监控的标的（持仓+候选池）

        # 统计
        self.stats = {
            "ticks_processed": 0,
            "signals_generated": 0,
            "orders_submitted": 0,
            "trades_filled": 0,
        }

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def start(self):
        """启动交易系统"""
        print("=" * 50)
        print("AStock Realtime Trading Loop")
        print("=" * 50)

        # 连接 Broker
        await self.broker.connect()

        # 启动事件总线
        await self.bus.start()

        # 风控日初重置
        self.risk.reset_daily()

        # 盘前初始化
        await self._pre_market_init()

        self.state = MarketState.PRE_MARKET
        self._running = True
        self._task = asyncio.create_task(self._main_loop())

        print(f"[Loop] 启动完成，监控标的: {len(self._watch_symbols)} 只")

    async def stop(self):
        """停止交易系统"""
        print("\n[Loop] 正在停止...")
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.bus.stop()
        await self.broker.disconnect()
        self._print_stats()
        print("[Loop] 已停止")

    # ------------------------------------------------------------------
    # 盘前初始化
    # ------------------------------------------------------------------
    async def _pre_market_init(self):
        """盘前：加载策略候选池"""
        print("\n[Loop] 盘前初始化...")
        ctx = {"data": self.data, "trade_date": self._trade_date}

        for st in self.strategies:
            try:
                st.on_init(ctx)
                # 收集候选标的
                if isinstance(st, LegacyStrategyAdapter):
                    self._watch_symbols.update(st.candidate_symbols)
            except Exception as e:
                print(f"[Loop] 策略 {st.name} 初始化失败: {e}")

        # 加载当前持仓到监控列表
        positions = await self.broker.query_positions()
        self._watch_symbols.update(positions.keys())

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    async def _main_loop(self):
        """主循环：定时轮询 + 状态机"""
        while self._running:
            now = datetime.now().time()
            self._update_market_state(now)

            if self.state in (MarketState.OPEN, MarketState.INTRADAY):
                await self._process_market()
            elif self.state == MarketState.CLOSE:
                await self._post_market()
                break
            else:
                # 盘前/竞价阶段等待
                pass

            await asyncio.sleep(self.poll_interval)

    def _update_market_state(self, now: time):
        """根据当前时间更新市场状态（简化版，未处理节假日）"""
        if now < time(9, 25):
            new_state = MarketState.PRE_MARKET
        elif time(9, 25) <= now < time(9, 30):
            new_state = MarketState.AUCTION
        elif time(9, 30) <= now < time(11, 30):
            new_state = MarketState.INTRADAY
        elif time(11, 30) <= now < time(13, 0):
            new_state = MarketState.INTRADAY  # 午休继续（不处理）
        elif time(13, 0) <= now < time(14, 57):
            new_state = MarketState.INTRADAY
        elif time(14, 57) <= now < time(15, 0):
            new_state = MarketState.INTRADAY  # 尾盘集合竞价
        else:
            new_state = MarketState.CLOSE

        if new_state != self.state:
            old = self.state.name
            self.state = new_state
            print(f"[Loop] 市场状态: {old} -> {new_state.name}")

    # ------------------------------------------------------------------
    # 盘中处理
    # ------------------------------------------------------------------
    async def _process_market(self):
        """轮询行情 → 驱动策略 → 下单"""
        # 1. 获取行情快照
        snapshot = await self._fetch_snapshot()
        if snapshot.empty:
            return

        # 2. 更新持仓市值（用于 Broker 模拟盘撮合）
        positions = await self.broker.query_positions()
        for sym, pos in positions.items():
            if sym in snapshot.index:
                price = float(snapshot.loc[sym, "price"])
                self.broker.update_position_price(sym, price)
                # 同时用 tick 撮合活跃订单
                self.broker.on_tick(sym, price)

        # 3. 对每个监控标的驱动策略
        for symbol in list(self._watch_symbols):
            if symbol not in snapshot.index:
                continue

            row = snapshot.loc[symbol]
            tick = Tick(
                symbol=symbol,
                price=float(row.get("price", 0)),
                volume=int(row.get("volume", 0)),
                bid1=float(row.get("bid1", 0)),
                ask1=float(row.get("ask1", 0)),
            )
            if tick.price <= 0:
                continue

            self.stats["ticks_processed"] += 1
            ctx = {"data": self.data, "trade_date": self._trade_date}

            # 3a. 检查持仓退出（止盈止损）
            if symbol in positions:
                for st in self.strategies:
                    sig = st.should_exit(ctx, positions[symbol], tick)
                    if sig:
                        await self._execute_signal(sig, positions, tick)
                        break

            # 3b. 检查入场信号
            for st in self.strategies:
                sig = st.on_tick(ctx, tick)
                if sig:
                    await self._execute_signal(sig, positions, tick)
                    break

        # 4. 发布定时事件（用于心跳、日志）
        await self.bus.publish(Event(EventType.TIMER, payload={"time": datetime.now()}, source="loop"))

    async def _execute_signal(self, signal: Signal, positions: Dict, tick: Tick):
        """信号 → 风控 → 下单"""
        self.stats["signals_generated"] += 1

        # 查询账户
        account = await self.broker.query_cash()
        active_orders = self.order_manager.get_active_orders(signal.symbol)

        # 风控检查
        passed, reason = self.risk.check_signal(
            signal,
            account.__dict__,
            positions,
            active_orders,
        )
        if not passed:
            print(f"[Risk] ❌ {signal.symbol} {signal.action.value} 被拦截: {reason}")
            return

        # 构建 TradePlan（复用现有数据结构）
        plan = signal.to_trade_plan() if hasattr(signal, "to_trade_plan") else self._signal_to_plan(signal, tick)

        # 卖出时计算数量
        sell_volume = 0
        if signal.action == PlanAction.SELL:
            pos = positions.get(signal.symbol)
            if pos:
                sell_volume = pos.get("volume", 0)
            if sell_volume <= 0:
                print(f"[Loop] {signal.symbol} 无持仓，跳过卖出信号")
                return

        # 提交订单
        price = signal.price
        order = await self.order_manager.submit_plan(plan, price=price, volume=sell_volume)
        if order:
            self.stats["orders_submitted"] += 1
            print(
                f"[Order] ✅ {order.side.value} {order.name}({order.symbol}) "
                f"{order.volume}股 @ {order.price} | 策略: {order.strategy}"
            )

            # 买入成功后加入监控列表
            if signal.action == PlanAction.BUY and signal.symbol not in self._watch_symbols:
                self._watch_symbols.add(signal.symbol)

    def _signal_to_plan(self, signal: Signal, tick: Tick):
        """Signal 转 TradePlan"""
        from astock.signal.plan import TradePlan
        return TradePlan(
            symbol=signal.symbol,
            name=signal.name,
            action=signal.action,
            strategy=signal.strategy,
            entry_price=signal.price,
            stop_loss=signal.stop_loss,
            stop_profit=signal.stop_profit,
            confidence=signal.confidence,
            reason=signal.reason,
            suggested_position=signal.suggested_position,
        )

    # ------------------------------------------------------------------
    # 行情获取
    # ------------------------------------------------------------------
    async def _fetch_snapshot(self) -> pd.DataFrame:
        """获取全市场快照并过滤到监控标的"""
        try:
            df = self.data.get_stock_info()
            if df.empty:
                return pd.DataFrame()
            # 统一列名兼容
            if "symbol" in df.columns:
                df = df.set_index("symbol")
            # 价格列可能是 close / price / 最新价
            if "price" not in df.columns and "close" in df.columns:
                df["price"] = df["close"]
            if "price" not in df.columns:
                return pd.DataFrame()
            # 只保留监控标的（减少计算量）
            if self._watch_symbols:
                df = df[df.index.isin(self._watch_symbols)]
            self._last_snapshot = df
            return df
        except Exception as e:
            print(f"[Loop] 行情获取失败: {e}")
            return self._last_snapshot

    # ------------------------------------------------------------------
    # 盘后结算
    # ------------------------------------------------------------------
    async def _post_market(self):
        """收盘后：撤单、结算、通知"""
        print("\n[Loop] 盘后结算...")
        await self.order_manager.cancel_all()

        account = await self.broker.query_cash()
        positions = await self.broker.query_positions()

        summary = (
            f"📅 收盘结算 [{self._trade_date}]\n"
            f"总资产: {account.total_asset:.2f}\n"
            f"现金: {account.cash:.2f}\n"
            f"持仓数: {len(positions)}\n"
            f"信号: {self.stats['signals_generated']} | 下单: {self.stats['orders_submitted']}"
        )
        print(summary)

        # 推送
        self.notifier.send(f"AStock 收盘结算 [{self._trade_date}]", summary)

    def _print_stats(self):
        print("\n[Loop] 运行统计:")
        for k, v in self.stats.items():
            print(f"  {k}: {v}")


# ------------------------------------------------------------------
# 便捷构造器
# ------------------------------------------------------------------
def build_loop_with_legacy_strategies(
    legacy_strategies,
    broker: Optional[PaperBroker] = None,
    data: Optional[DataClient] = None,
    poll_interval: float = 5.0,
) -> TradingLoop:
    """
    用旧策略快速构建实时交易循环

    Usage:
        from astock.strategy.zt_strategies import FirstBoardStrategy
        loop = build_loop_with_legacy_strategies([FirstBoardStrategy()])
        asyncio.run(loop.start())
    """
    adapters = [
        LegacyStrategyAdapter(st) for st in legacy_strategies
    ]
    return TradingLoop(
        strategies=adapters,
        broker=broker,
        data=data,
        poll_interval=poll_interval,
    )
