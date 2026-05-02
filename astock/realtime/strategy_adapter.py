"""
实时策略适配器 (Strategy Adapter)
==================================
将现有盘后策略（StrategyBase）包装为事件驱动策略，
同时定义新的实时策略基类（RealtimeStrategy）。

适配逻辑：
- 开盘前：调用旧策略 select_candidates() 生成候选池
- 盘中 tick：监控候选标的价格，触发买入信号
- 持仓监控：监控已持仓标的止盈止损，触发卖出信号
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime

import pandas as pd

from astock.strategy.base import StrategyBase
from astock.signal.plan import TradePlan, PlanAction
from astock.realtime.event_bus import Event, EventType


@dataclass
class Tick:
    """简化 Tick 数据结构"""
    symbol: str
    price: float
    volume: int = 0
    bid1: float = 0.0
    ask1: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Signal:
    """策略信号（由策略产生，送往风控/订单模块）"""
    symbol: str
    name: str
    action: PlanAction
    price: float
    volume: int = 0
    confidence: float = 0.0
    strategy: str = ""
    reason: str = ""
    stop_loss: float = 0.0
    stop_profit: float = 0.0
    suggested_position: float = 0.0


class RealtimeStrategy(ABC):
    """
    实时策略基类

    子类可实现：
    - on_init(ctx)         : 开盘前初始化（加载候选池、预热数据）
    - on_tick(ctx, tick)   : 逐笔行情响应
    - on_bar(ctx, bar)     : K线响应
    - on_trade(ctx, trade) : 成交回报响应（用于反手、加仓逻辑）
    - should_exit(ctx, position, tick) -> Optional[Signal] : 持仓退出判断
    """

    name: str = "realtime_base"

    @abstractmethod
    def on_init(self, ctx: Dict[str, Any]):
        """开盘前初始化"""
        pass

    def on_tick(self, ctx: Dict[str, Any], tick: Tick) -> Optional[Signal]:
        """响应逐笔行情，返回信号或 None"""
        return None

    def on_bar(self, ctx: Dict[str, Any], bar: Dict[str, Any]) -> Optional[Signal]:
        """响应 K 线，返回信号或 None"""
        return None

    def on_trade(self, ctx: Dict[str, Any], trade: Dict[str, Any]):
        """成交回报回调"""
        pass

    def should_exit(self, ctx: Dict[str, Any], position: Dict[str, Any], tick: Tick) -> Optional[Signal]:
        """判断持仓是否应退出（止盈止损）"""
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name}


class LegacyStrategyAdapter(RealtimeStrategy):
    """
    旧策略适配器

    将基于 DataFrame 的盘后策略包装为事件驱动：
    1. 开盘前运行 select_candidates，缓存候选 DataFrame
    2. 盘中每个 tick 检查：候选标的是否满足买入条件（价格触发）
    3. 持仓检查止盈止损
    """

    def __init__(self, strategy: StrategyBase, trade_date: Optional[str] = None):
        self.strategy = strategy
        self.trade_date = trade_date or datetime.now().strftime("%Y%m%d")
        self.name = f"adapter_{strategy.name}"

        # 候选池
        self.candidates: pd.DataFrame = pd.DataFrame()
        self.candidate_symbols: set = set()

        # 已触发买入的标的（避免重复）
        self.triggered: set = set()

        # 止盈止损映射 {symbol: {"stop_loss": float, "stop_profit": float}}
        self.exit_levels: Dict[str, Dict[str, float]] = {}

    # ------------------------------------------------------------------
    # 初始化：运行旧策略选股
    # ------------------------------------------------------------------
    def on_init(self, ctx: Dict[str, Any]):
        data = ctx.get("data")
        if data is None:
            print(f"[{self.name}] init skipped: no data client")
            return

        try:
            df = self.strategy.select_candidates(data, self.trade_date)
            if not df.empty:
                self.candidates = df
                self.candidate_symbols = set(df["symbol"].tolist())
                # 记录止盈止损
                for _, row in df.iterrows():
                    sym = str(row.get("symbol", ""))
                    if sym:
                        self.exit_levels[sym] = {
                            "stop_loss": row.get("stop_loss", 0.0) or 0.0,
                            "stop_profit": row.get("stop_profit", 0.0) or 0.0,
                        }
                print(f"[{self.name}] 候选池加载: {len(df)} 只")
            else:
                print(f"[{self.name}] 今日无候选")
        except Exception as e:
            print(f"[{self.name}] select_candidates 失败: {e}")

    # ------------------------------------------------------------------
    # 盘中 tick：检查买入触发
    # ------------------------------------------------------------------
    def on_tick(self, ctx: Dict[str, Any], tick: Tick) -> Optional[Signal]:
        if tick.symbol in self.triggered:
            return None
        if tick.symbol not in self.candidate_symbols:
            return None

        row = self.candidates[self.candidates["symbol"] == tick.symbol]
        if row.empty:
            return None
        row = row.iloc[0]

        # 买入触发：当前价 >= entry_price（涨停价买入逻辑）
        entry_price = row.get("entry_price") or row.get("close")
        if entry_price and tick.price >= entry_price * 0.995:  # 允许 0.5% 滑点
            self.triggered.add(tick.symbol)
            return Signal(
                symbol=tick.symbol,
                name=str(row.get("name", tick.symbol)),
                action=PlanAction.BUY,
                price=tick.price,
                confidence=float(row.get("confidence", 0.5)),
                strategy=self.strategy.name,
                reason=str(row.get("reason", "")),
                stop_loss=float(row.get("stop_loss", 0.0) or 0.0),
                stop_profit=float(row.get("stop_profit", 0.0) or 0.0),
                suggested_position=float(row.get("suggested_position", 0.0) or 0.0),
            )
        return None

    # ------------------------------------------------------------------
    # 持仓退出检查
    # ------------------------------------------------------------------
    def should_exit(self, ctx: Dict[str, Any], position: Dict[str, Any], tick: Tick) -> Optional[Signal]:
        symbol = position.get("symbol", "")
        if not symbol:
            return None

        entry_price = position.get("cost_price", 0.0)
        current_price = tick.price
        if entry_price <= 0 or current_price <= 0:
            return None

        # 动态止盈止损：优先使用策略自带的水平，否则用持仓记录
        levels = self.exit_levels.get(symbol, {})
        stop_loss = levels.get("stop_loss", 0.0)
        stop_profit = levels.get("stop_profit", 0.0)

        # 如果策略没指定，按百分比计算（这里用简单 7% 止损 / 15% 止盈）
        if stop_loss <= 0:
            stop_loss = entry_price * 0.93
        if stop_profit <= 0:
            stop_profit = entry_price * 1.15

        reason = ""
        if current_price <= stop_loss:
            reason = f"触发止损: 成本{entry_price} 现价{current_price} 止损线{stop_loss:.2f}"
        elif current_price >= stop_profit:
            reason = f"触发止盈: 成本{entry_price} 现价{current_price} 止盈线{stop_profit:.2f}"

        if reason:
            return Signal(
                symbol=symbol,
                name=position.get("name", symbol),
                action=PlanAction.SELL,
                price=current_price,
                strategy=f"{self.strategy.name}_exit",
                reason=reason,
            )
        return None
