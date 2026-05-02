"""交易计划数据结构"""
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class PlanAction(str, Enum):
    BUY = "买入"
    SELL = "卖出"
    HOLD = "持有"
    WATCH = "观察"


@dataclass
class TradePlan:
    """单笔交易计划"""
    symbol: str                      # 股票代码
    name: str                        # 股票名称
    action: PlanAction               # 动作
    strategy: str                    # 来源策略

    # 价格相关
    entry_price: Optional[float] = None      # 建议买入价（涨停价或现价）
    stop_loss: Optional[float] = None        # 止损价
    stop_profit: Optional[float] = None      # 止盈价
    dynamic_stop: Optional[float] = None     # 动态止盈（如炸板减半）

    # 仓位与评分
    confidence: float = 0.0          # 置信度 0-1
    score: float = 0.0               # 策略原始分
    suggested_position: float = 0.0  # 建议仓位（占总资金比例）

    # 理由与备注
    reason: str = ""                 # 买入/卖出理由
    risk_note: str = ""              # 风险提示
    industry: Optional[str] = None   # 所属行业

    # 状态跟踪（手动交易用）
    status: str = "pending"          # pending / executed / cancelled / closed
    executed_price: Optional[float] = None
    executed_time: Optional[str] = None

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "name": self.name,
            "action": self.action.value,
            "strategy": self.strategy,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "stop_profit": self.stop_profit,
            "dynamic_stop": self.dynamic_stop,
            "confidence": self.confidence,
            "score": self.score,
            "suggested_position": self.suggested_position,
            "reason": self.reason,
            "risk_note": self.risk_note,
            "industry": self.industry,
            "status": self.status,
            "executed_price": self.executed_price,
            "executed_time": self.executed_time,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TradePlan":
        return cls(
            symbol=d["symbol"],
            name=d["name"],
            action=PlanAction(d["action"]),
            strategy=d["strategy"],
            entry_price=d.get("entry_price"),
            stop_loss=d.get("stop_loss"),
            stop_profit=d.get("stop_profit"),
            dynamic_stop=d.get("dynamic_stop"),
            confidence=d.get("confidence", 0.0),
            score=d.get("score", 0.0),
            suggested_position=d.get("suggested_position", 0.0),
            reason=d.get("reason", ""),
            risk_note=d.get("risk_note", ""),
            industry=d.get("industry"),
            status=d.get("status", "pending"),
            executed_price=d.get("executed_price"),
            executed_time=d.get("executed_time"),
        )
