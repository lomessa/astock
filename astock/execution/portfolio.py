"""
持仓管理
========
基于本地 JSON 的简易持仓系统，支持手动录入与盈亏跟踪。
"""
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime

import pandas as pd

from astock.data.client import DataClient


@dataclass
class Position:
    """持仓记录"""
    symbol: str
    name: str
    volume: int = 0              # 持股数量
    entry_price: float = 0.0     # 成本价
    entry_date: str = ""         # 买入日期
    current_price: float = 0.0   # 最新价
    market_value: float = 0.0    # 市值
    unrealized_pnl: float = 0.0  # 浮动盈亏
    unrealized_pnl_pct: float = 0.0  # 浮动盈亏%
    stop_loss: float = 0.0       # 止损价
    stop_profit: float = 0.0     # 止盈价
    strategy: str = ""           # 来源策略
    notes: str = ""              # 备注

    def update_price(self, price: float):
        self.current_price = price
        self.market_value = self.volume * price
        self.unrealized_pnl = self.market_value - self.volume * self.entry_price
        if self.entry_price > 0:
            self.unrealized_pnl_pct = self.unrealized_pnl / (self.volume * self.entry_price)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Position":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class Portfolio:
    """持仓管理器"""

    def __init__(self, data: Optional[DataClient] = None, db_path: Optional[str] = None):
        self.data = data or DataClient()
        self.db_path = Path(db_path or (Path.home() / ".astock" / "portfolio.json"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.positions: Dict[str, Position] = {}
        self.cash: float = 0.0
        self.total_asset: float = 0.0
        self._load()

    def _load(self):
        """从磁盘加载"""
        if self.db_path.exists():
            with open(self.db_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
                self.cash = raw.get("cash", 0.0)
                self.total_asset = raw.get("total_asset", self.cash)
                for item in raw.get("positions", []):
                    pos = Position.from_dict(item)
                    self.positions[pos.symbol] = pos
        else:
            self.cash = 100000.0  # 默认 10 万模拟资金
            self.total_asset = self.cash

    def save(self):
        """保存到磁盘"""
        payload = {
            "cash": self.cash,
            "total_asset": self.total_asset,
            "updated_at": datetime.now().isoformat(),
            "positions": [p.to_dict() for p in self.positions.values()],
        }
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def buy(self, symbol: str, name: str, price: float, volume: int,
            stop_loss: float = 0.0, stop_profit: float = 0.0,
            strategy: str = "", notes: str = ""):
        """买入（手动录入）"""
        cost = price * volume
        if cost > self.cash:
            raise ValueError(f"现金不足: 需要 {cost}, 剩余 {self.cash}")

        if symbol in self.positions:
            pos = self.positions[symbol]
            total_cost = pos.entry_price * pos.volume + cost
            pos.volume += volume
            pos.entry_price = total_cost / pos.volume
        else:
            pos = Position(
                symbol=symbol,
                name=name,
                volume=volume,
                entry_price=price,
                entry_date=datetime.now().strftime("%Y%m%d"),
                current_price=price,
                market_value=cost,
                stop_loss=stop_loss,
                stop_profit=stop_profit,
                strategy=strategy,
                notes=notes,
            )
            self.positions[symbol] = pos

        self.cash -= cost
        self.save()
        print(f"[买入] {name}({symbol}) {volume}股 @ {price}, 止损={stop_loss}, 止盈={stop_profit}")

    def sell(self, symbol: str, price: float, volume: Optional[int] = None):
        """卖出（手动录入）"""
        if symbol not in self.positions:
            raise ValueError(f"未持有 {symbol}")
        pos = self.positions[symbol]
        vol = volume or pos.volume
        if vol > pos.volume:
            raise ValueError(f"卖出数量超过持仓: {vol} > {pos.volume}")

        proceeds = price * vol
        self.cash += proceeds
        pos.volume -= vol
        if pos.volume == 0:
            del self.positions[symbol]
        else:
            pos.update_price(price)

        self.save()
        print(f"[卖出] {symbol} {vol}股 @ {price}, 回笼资金 {proceeds:.2f}")

    def update_market(self):
        """更新所有持仓最新价（盘中调用）"""
        if not self.positions:
            return
        symbols = list(self.positions.keys())
        df = self.data.get_stock_info()
        if df.empty or "symbol" not in df.columns:
            return
        df = df[df["symbol"].isin(symbols)].set_index("symbol")

        total_mv = 0.0
        for sym, pos in self.positions.items():
            if sym in df.index:
                price = float(df.loc[sym, "price"]) if pd.notna(df.loc[sym, "price"]) else pos.current_price
                pos.update_price(price)
                total_mv += pos.market_value
            else:
                total_mv += pos.market_value

        self.total_asset = self.cash + total_mv
        self.save()

    def get_alerts(self) -> List[Dict[str, Any]]:
        """检查止损止盈预警"""
        alerts = []
        for sym, pos in self.positions.items():
            if pos.current_price <= 0:
                continue
            pnl_pct = pos.unrealized_pnl_pct
            if pos.current_price <= pos.stop_loss:
                alerts.append({
                    "symbol": sym,
                    "name": pos.name,
                    "type": "止损",
                    "current_price": pos.current_price,
                    "trigger_price": pos.stop_loss,
                    "pnl_pct": pnl_pct,
                })
            elif pos.current_price >= pos.stop_profit:
                alerts.append({
                    "symbol": sym,
                    "name": pos.name,
                    "type": "止盈",
                    "current_price": pos.current_price,
                    "trigger_price": pos.stop_profit,
                    "pnl_pct": pnl_pct,
                })
        return alerts

    def summary(self) -> Dict[str, Any]:
        """账户概览"""
        self.update_market()
        return {
            "cash": round(self.cash, 2),
            "total_asset": round(self.total_asset, 2),
            "market_value": round(self.total_asset - self.cash, 2),
            "holding_count": len(self.positions),
            "positions": [p.to_dict() for p in self.positions.values()],
        }

    def print_summary(self):
        """打印持仓"""
        s = self.summary()
        print(f"\n💰 账户概览")
        print(f"总资产: {s['total_asset']:.2f}  现金: {s['cash']:.2f}  持仓市值: {s['market_value']:.2f}")
        if s["positions"]:
            df = pd.DataFrame(s["positions"])
            df = df[["symbol", "name", "volume", "entry_price", "current_price",
                     "unrealized_pnl", "unrealized_pnl_pct", "stop_loss", "stop_profit", "strategy"]]
            print(df.to_string(index=False))
        else:
            print("当前无持仓")
        alerts = self.get_alerts()
        if alerts:
            print("\n⚠️ 预警:")
            for a in alerts:
                print(f"  {a['type']}: {a['name']}({a['symbol']}) 现价{a['current_price']} 盈亏{a['pnl_pct']:.2%}")
