"""
信号引擎
========
整合多策略输出，经风控过滤、打分排序，生成可执行的交易计划。
"""
from typing import List, Optional, Any
from datetime import datetime

import pandas as pd
import numpy as np

from astock.data import DataClient
from astock.strategy.base import StrategyBase
from astock.risk.manager import RiskManager
from astock.signal.plan import TradePlan, PlanAction
from astock.config import get_settings


class SignalEngine:
    """信号引擎：多策略 → 风控过滤 → 交易计划"""

    def __init__(self, data: Optional[Any] = None, strategies: Optional[List[StrategyBase]] = None, gp_booster=None):
        self.data = data or DataClient()
        self.strategies = strategies or []
        self.risk = RiskManager()
        self.cfg = get_settings()
        self.gp_booster = gp_booster

    def generate_plans(self, trade_date: Optional[str] = None) -> List[TradePlan]:
        """
        生成当日交易计划

        Returns
        -------
        List[TradePlan]
        """
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")

        all_candidates: List[pd.DataFrame] = []
        for st in self.strategies:
            try:
                df = st.select_candidates(self.data, trade_date)
                if not df.empty:
                    all_candidates.append(df)
            except Exception as e:
                print(f"[ERROR] 策略 {st.name} 运行失败: {e}")

        if not all_candidates:
            return []

        combined = pd.concat(all_candidates, ignore_index=True)

        # 去重：同一只票多个策略，取最高分
        if "symbol" in combined.columns:
            combined = combined.sort_values("score", ascending=False).drop_duplicates(subset=["symbol"], keep="first")

        # GP 因子增强（过滤 + 加分）
        if self.gp_booster is not None:
            try:
                combined = self.gp_booster.filter_candidates(combined, trade_date)
                if combined.empty:
                    print("[GP] 过滤后无候选，放宽条件重试...")
                    combined = self.gp_booster.filter_candidates(
                        pd.concat(all_candidates, ignore_index=True), trade_date, top_pct=0.8, score_boost=0.05
                    )
            except Exception as e:
                print(f"[WARN] GP 因子过滤失败: {e}")

        # 风控过滤
        combined = self.risk.filter_candidates(combined)

        # 生成交易计划
        plans = []
        for _, row in combined.iterrows():
            plan = self._row_to_plan(row)
            plans.append(plan)

        # 按置信度排序
        plans.sort(key=lambda p: p.confidence, reverse=True)
        return plans

    def _row_to_plan(self, row: pd.Series) -> TradePlan:
        """DataFrame 行 → TradePlan"""
        symbol = str(row.get("symbol", ""))
        name = str(row.get("name", ""))
        close = float(row.get("close", 0) or 0)
        confidence = float(row.get("confidence", 0) or 0)
        score = float(row.get("score", 0) or 0)
        reason = str(row.get("reason", ""))
        strategy = str(row.get("strategy", ""))
        industry = str(row.get("industry", "")) if pd.notna(row.get("industry")) else None

        # 计算价格
        entry = close
        # 涨停价（A股 10% / 20%）
        limit_pct = 0.2 if symbol.startswith(("68", "30")) else 0.1
        # 如果 close 已经是涨停价，entry 用涨停价；否则建议挂涨停价买入
        entry = round(close * (1 + limit_pct), 2)

        # 止损止盈（策略可自定义，未定义则使用全局默认值）
        sl = round(row.get("stop_loss", entry * (1 - self.cfg.risk.stop_loss_ratio)), 2)
        sp = round(row.get("stop_profit", entry * (1 + self.cfg.risk.stop_profit_ratio)), 2)
        dynamic = round(row.get("dynamic_stop", entry * (1 + self.cfg.risk.dynamic_stop_profit)), 2)

        # 仓位建议（按置信度分档）
        if confidence >= 0.85:
            pos = self.cfg.risk.max_position_per_stock
        elif confidence >= 0.70:
            pos = self.cfg.risk.max_position_per_stock * 0.7
        elif confidence >= 0.55:
            pos = self.cfg.risk.max_position_per_stock * 0.4
        else:
            pos = self.cfg.risk.max_position_per_stock * 0.2

        pos = round(pos, 2)

        # 风险提示
        risk_notes = []
        if row.get("board_num", 0) and row.get("board_num", 0) >= 4:
            risk_notes.append("高位连板，注意天地板风险")
        if row.get("open_times", 0) and row.get("open_times", 0) >= 2:
            risk_notes.append("开板次数多，筹码不稳")
        if row.get("float_mv", 999) and row.get("float_mv", 0) > 150:
            risk_notes.append("流通市值偏大，连板难度大")

        return TradePlan(
            symbol=symbol,
            name=name,
            action=PlanAction.BUY,
            strategy=strategy,
            entry_price=entry,
            stop_loss=sl,
            stop_profit=sp,
            dynamic_stop=dynamic,
            confidence=confidence,
            score=score,
            suggested_position=pos,
            reason=reason,
            risk_note="；".join(risk_notes) if risk_notes else "",
            industry=industry,
        )

    def format_plans(self, plans: List[TradePlan]) -> str:
        """将交易计划格式化为可读文本"""
        if not plans:
            return "今日无交易信号。"
        lines = [f"📅 交易计划 [{datetime.now().strftime('%Y-%m-%d %H:%M')}]\n"]
        for i, p in enumerate(plans[:15], 1):
            lines.append(
                f"{i}. 【{p.action.value}】{p.name} ({p.symbol}) | {p.strategy}\n"
                f"   置信度: {p.confidence:.0%} | 建议仓位: {p.suggested_position:.0%}\n"
                f"   买入价: ¥{p.entry_price} | 止损: ¥{p.stop_loss} | 止盈: ¥{p.stop_profit}\n"
                f"   理由: {p.reason}\n"
                f"   风险: {p.risk_note or '无'}\n"
            )
        return "\n".join(lines)
