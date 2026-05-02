"""
实时风控适配器 (Realtime Risk Adapter)
=======================================
将现有静态风控（RiskManager.filter_candidates）扩展为盘中逐单检查。

检查维度：
1. 标的级：黑名单、ST、价格异常、市值
2. 订单级：重复下单、反向锁仓（同标的同时挂买卖单）
3. 账户级：单日亏损上限、持仓数量上限、单票仓位上限
4. 市场级：大盘熔断/暴跌暂停（预留）
"""
from typing import Any, Dict, List, Optional, Tuple

from astock.risk.manager import RiskManager
from astock.config import get_settings
from astock.realtime.strategy_adapter import Signal
from astock.signal.plan import PlanAction


class RealtimeRiskManager:
    """实时风控管理器"""

    def __init__(self):
        self.cfg = get_settings().risk
        self.static_risk = RiskManager()
        # 日内追踪
        self.daily_pnl: float = 0.0          # 当日累计盈亏（简化）
        self.locked: bool = False            # 账户自锁（触发日亏损上限后）
        self.rejected_count: int = 0         # 今日被拒单数（异常监控）

    # ------------------------------------------------------------------
    # 下单前检查
    # ------------------------------------------------------------------
    def check_signal(
        self,
        signal: Signal,
        account: Dict[str, Any],
        positions: Dict[str, Dict[str, Any]],
        active_orders: List[Any],
    ) -> Tuple[bool, str]:
        """
        对策略信号进行实时风控检查

        Returns
        -------
        (bool, str)
            (是否通过, 拒绝原因)
        """
        symbol = signal.symbol
        side = signal.action

        # 0. 账户自锁
        if self.locked:
            return False, "账户已触发日亏损上限，自动锁定"

        # 1. 黑名单
        if self.cfg.blacklist and symbol in self.cfg.blacklist:
            return False, f"黑名单标的: {symbol}"

        # 2. 持仓数量上限
        holding_count = len(positions)
        if side == PlanAction.BUY and symbol not in positions and holding_count >= self.cfg.max_holding_count:
            return False, f"持仓数量已达上限: {self.cfg.max_holding_count}"

        # 3. 重复下单检查：该标的有活跃挂单时拒绝
        for o in active_orders:
            if o.symbol == symbol and not o.is_done:
                return False, f"{symbol} 已有活跃订单"

        # 4. 反向锁仓检查：同标的同时挂买卖单（简化：只检查活跃订单）
        for o in active_orders:
            if o.symbol == symbol and not o.is_done:
                if (side == PlanAction.BUY and o.side.value == "卖出") or \
                   (side == PlanAction.SELL and o.side.value == "买入"):
                    return False, f"{symbol} 存在反向挂单，避免锁仓"

        # 5. 单票仓位上限（买入时）
        if side == PlanAction.BUY:
            pos = positions.get(symbol)
            pos_mv = pos.get("market_value", 0.0) if pos else 0.0
            total_asset = account.get("total_asset", 1.0)
            new_amount = signal.price * (signal.volume or 0)
            if total_asset > 0 and (pos_mv + new_amount) / total_asset > self.cfg.max_position_per_stock:
                return False, (
                    f"{symbol} 将超过单票仓位上限 "
                    f"({self.cfg.max_position_per_stock:.0%})"
                )

        # 6. 卖出时检查是否有持仓
        if side == PlanAction.SELL and symbol not in positions:
            return False, f"{symbol} 无持仓，无法卖出"

        # 7. 价格异常
        if signal.price <= 0 or signal.price > 1000:
            return False, f"价格异常: {signal.price}"

        return True, "通过"

    # ------------------------------------------------------------------
    # 成交后更新（用于追踪日盈亏、触发账户自锁）
    # ------------------------------------------------------------------
    def on_trade(self, trade: Dict[str, Any], positions: Dict[str, Dict[str, Any]], total_asset: float):
        """
        根据成交回报更新日内风控状态

        Parameters
        ----------
        trade : dict
            {"symbol", "side", "price", "volume", "pnl"}
        total_asset : float
            当前总资产（用于计算回撤比例）
        """
        # 简化：外部传入 estimated pnl
        pnl = trade.get("pnl", 0.0)
        self.daily_pnl += pnl

        # 检查日亏损上限
        if total_asset > 0 and self.daily_pnl / total_asset <= -self.cfg.max_daily_loss_ratio:
            if not self.locked:
                self.locked = True
                print(
                    f"[RealtimeRisk] ⚠️ 触发日亏损上限锁定: "
                    f"日盈亏 {self.daily_pnl:.2f} ({self.daily_pnl/total_asset:.2%})"
                )

    def reset_daily(self):
        """每日重置"""
        self.daily_pnl = 0.0
        self.locked = False
        self.rejected_count = 0
