"""交易计划持久化与跟踪"""
import json
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from astock.signal.plan import TradePlan, PlanAction


class PlanManager:
    """管理每日交易计划"""

    def __init__(self, db_dir: Optional[str] = None):
        self.db_dir = Path(db_dir or (Path.home() / ".astock" / "plans"))
        self.db_dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self, trade_date: str) -> Path:
        return self.db_dir / f"{trade_date}.json"

    def save_plans(self, plans: List[TradePlan], trade_date: Optional[str] = None):
        """保存当日计划"""
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")
        path = self._file_path(trade_date)
        with open(path, "w", encoding="utf-8") as f:
            json.dump([p.to_dict() for p in plans], f, ensure_ascii=False, indent=2)

    def load_plans(self, trade_date: Optional[str] = None) -> List[TradePlan]:
        """读取当日计划"""
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")
        path = self._file_path(trade_date)
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
            return [TradePlan.from_dict(item) for item in raw]

    def update_plan_status(self, symbol: str, status: str, trade_date: Optional[str] = None,
                           executed_price: Optional[float] = None):
        """更新计划状态（手动成交后调用）"""
        plans = self.load_plans(trade_date)
        for p in plans:
            if p.symbol == symbol:
                p.status = status
                if executed_price is not None:
                    p.executed_price = executed_price
                    p.executed_time = datetime.now().isoformat()
        self.save_plans(plans, trade_date)
