#!/usr/bin/env python3
"""
Mock 演示脚本
==============
不依赖 pandas/akshare，用纯 Python 模拟数据，展示框架运行流程与输出格式。
"""
import argparse
import json
import random
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional


# ==================================================================
# Mock 数据层
# ==================================================================
class MockData:
    """模拟 AKShare 数据"""

    INDUSTRIES = ["人工智能", "机器人", "半导体", "新能源", "光伏", "军工", "医药", "金融", "地产", "消费"]
    NAMES = ["恒银科技", "中科曙光", "剑桥科技", "拓维信息", "三六零", "昆仑万维", "浪潮信息", "科大讯飞",
             "中兴通讯", "工业富联", "比亚迪", "宁德时代", "贵州茅台", "招商银行", "东方财富",
             "中信证券", "中国平安", "万科A", "保利发展", "美的集团"]

    def __init__(self, seed: int = 42):
        random.seed(seed)

    def _rand_symbol(self):
        return f"{random.randint(0, 6):01d}{random.randint(0, 999999):06d}"

    def get_zt_pool(self, trade_date: Optional[str] = None) -> List[Dict]:
        """模拟今日涨停池 80 只"""
        results = []
        for i in range(80):
            board_num = random.choices([1, 2, 3, 4, 5, 6], weights=[40, 20, 10, 5, 3, 2])[0]
            open_times = random.choices([0, 1, 2, 3], weights=[50, 30, 15, 5])[0]
            results.append({
                "symbol": self._rand_symbol(),
                "name": random.choice(self.NAMES),
                "close": round(random.uniform(5, 120), 2),
                "change_pct": round(9.8 + random.random() * 0.4, 2),
                "float_mv": round(random.uniform(10, 300), 2),
                "seal_amount": round(random.uniform(0.1, 8), 2),
                "open_times": open_times,
                "board_num": board_num,
                "turnover": round(random.uniform(1, 35), 2),
                "industry": random.choice(self.INDUSTRIES),
                "first_seal_time": f"09:{random.randint(30, 59):02d}",
                "last_seal_time": f"{random.randint(9, 14):02d}:{random.randint(30, 59):02d}",
            })
        # 固定几个高置信度标的，方便演示
        results[0] = {**results[0], "symbol": "002528", "name": "英飞拓", "board_num": 1,
                      "float_mv": 45, "seal_amount": 2.5, "open_times": 0, "turnover": 8.5,
                      "industry": "人工智能", "close": 12.88}
        results[1] = {**results[1], "symbol": "000977", "name": "浪潮信息", "board_num": 2,
                      "float_mv": 120, "seal_amount": 4.2, "open_times": 0, "turnover": 6.2,
                      "industry": "人工智能", "close": 38.50}
        results[2] = {**results[2], "symbol": "002229", "name": "鸿博股份", "board_num": 3,
                      "float_mv": 85, "seal_amount": 1.8, "open_times": 1, "turnover": 12.5,
                      "industry": "算力", "close": 25.60}
        return results

    def get_zt_pool_previous(self, trade_date: Optional[str] = None) -> List[Dict]:
        """模拟昨日涨停今日表现"""
        return [{"symbol": self._rand_symbol(), "name": random.choice(self.NAMES),
                 "change_pct": round(random.gauss(2.5, 4), 2)} for _ in range(60)]

    def get_zt_pool_zbgc(self, trade_date: Optional[str] = None) -> List[Dict]:
        """模拟炸板池 15 只"""
        return [{"symbol": self._rand_symbol(), "name": random.choice(self.NAMES),
                 "close": round(random.uniform(5, 80), 2),
                 "float_mv": round(random.uniform(20, 200), 2),
                 "seal_amount": round(random.uniform(0.2, 3), 2),
                 "open_times": random.randint(2, 5),
                 "industry": random.choice(self.INDUSTRIES),
                 "last_seal_time": f"{random.randint(9, 14):02d}:{random.randint(30, 59):02d}"} for _ in range(15)]

    def get_stock_info(self) -> List[Dict]:
        return []

    def get_index_daily(self, symbol: str = "000001") -> List[Dict]:
        base = 3200
        results = []
        for i in range(30):
            base += random.gauss(0, 15)
            results.append({"date": f"202604{random.randint(1,30):02d}", "close": round(base, 2)})
        return results


# ==================================================================
# Mock 策略层
# ==================================================================
class MockFirstBoard:
    name = "首板"
    def select(self, data: MockData):
        pool = data.get_zt_pool()
        candidates = [p for p in pool if p["board_num"] == 1 and p["float_mv"] <= 200
                      and 3 <= p["turnover"] <= 30 and p["seal_amount"] >= 0.3 and p["open_times"] <= 2]
        for c in candidates:
            c["confidence"] = round(min(1.0, 0.4 + c["seal_amount"]/10 + (1-c["float_mv"]/300)*0.3), 3)
            c["score"] = c["confidence"]
            c["reason"] = f"首板突破：流通市值{c['float_mv']}亿，封单{c['seal_amount']}亿，换手{c['turnover']}%"
            c["strategy"] = self.name
        return sorted(candidates, key=lambda x: x["confidence"], reverse=True)[:15]


class MockContinuousBoard:
    name = "连板接力"
    def select(self, data: MockData):
        pool = data.get_zt_pool()
        candidates = [p for p in pool if 2 <= p["board_num"] <= 5 and p["seal_amount"] >= 1 and p["open_times"] <= 1]
        for c in candidates:
            c["confidence"] = round(min(1.0, 0.5 + c["seal_amount"]/8 - abs(c["board_num"]-3)*0.1), 3)
            c["score"] = c["confidence"]
            c["reason"] = f"连板接力：{c['board_num']}连板，封单{c['seal_amount']}亿"
            c["strategy"] = self.name
        return sorted(candidates, key=lambda x: x["confidence"], reverse=True)[:10]


class MockReseal:
    name = "炸板回封"
    def select(self, data: MockData):
        pool = data.get_zt_pool_zbgc()
        candidates = [p for p in pool if p["open_times"] <= 3]
        for c in candidates:
            c["confidence"] = round(min(1.0, 0.5 + (3-c["open_times"])*0.1 + c["seal_amount"]/5), 3)
            c["score"] = c["confidence"]
            c["reason"] = f"炸板回封：开板{c['open_times']}次，回封后封单{c['seal_amount']}亿"
            c["strategy"] = self.name
        return sorted(candidates, key=lambda x: x["confidence"], reverse=True)[:8]


# ==================================================================
# Mock 信号引擎
# ==================================================================
@dataclass
class TradePlan:
    symbol: str
    name: str
    action: str
    strategy: str
    entry_price: float
    stop_loss: float
    stop_profit: float
    confidence: float
    suggested_position: float
    reason: str
    risk_note: str = ""


def generate_plans(data: MockData) -> List[TradePlan]:
    all_candidates = []
    all_candidates.extend(MockFirstBoard().select(data))
    all_candidates.extend(MockContinuousBoard().select(data))
    all_candidates.extend(MockReseal().select(data))

    # 去重
    seen = set()
    unique = []
    for c in all_candidates:
        if c["symbol"] not in seen:
            seen.add(c["symbol"])
            unique.append(c)
    unique.sort(key=lambda x: x["confidence"], reverse=True)

    plans = []
    for c in unique:
        entry = round(c["close"] * 1.1, 2)
        sl = round(entry * 0.93, 2)
        sp = round(entry * 1.15, 2)
        pos = 0.2 if c["confidence"] >= 0.85 else (0.14 if c["confidence"] >= 0.7 else 0.08)
        risk = []
        if c.get("board_num", 0) >= 4:
            risk.append("高位连板，注意天地板")
        if c.get("open_times", 0) >= 2:
            risk.append("筹码不稳")
        plans.append(TradePlan(
            symbol=c["symbol"], name=c["name"], action="买入", strategy=c["strategy"],
            entry_price=entry, stop_loss=sl, stop_profit=sp,
            confidence=c["confidence"], suggested_position=round(pos, 2),
            reason=c["reason"], risk_note="；".join(risk)
        ))
    return plans


# ==================================================================
# Mock 情绪分析
# ==================================================================
def sentiment_analysis(data: MockData) -> Dict:
    zt = data.get_zt_pool()
    zbgc = data.get_zt_pool_zbgc()
    prev = data.get_zt_pool_previous()
    avg_premium = sum(p["change_pct"] for p in prev) / len(prev) if prev else 0
    win_rate = sum(1 for p in prev if p["change_pct"] > 0) / len(prev) if prev else 0
    max_board = max(p["board_num"] for p in zt) if zt else 0
    blast_rate = len(zbgc) / (len(zt) + len(zbgc) + 1e-6)

    score = 50
    score += 15 if len(zt) >= 100 else (10 if len(zt) >= 60 else 5)
    score += 10 if max_board >= 6 else (5 if max_board >= 4 else 0)
    score += 10 if blast_rate < 0.15 else (5 if blast_rate < 0.25 else -15)
    score += 10 if avg_premium > 3 else (5 if avg_premium > 1 else -10)
    score = max(0, min(100, score))

    temp = "沸腾" if score >= 80 else ("高温" if score >= 60 else ("温和" if score >= 40 else ("低温" if score >= 20 else "冰冷")))
    sug = {80: "积极出手，重仓参与核心", 60: "可出手，控制仓位", 40: "轻仓试错或观望", 20: "观望为主", 0: "空仓休息"}
    suggestion = sug[80] if score >= 80 else sug[60] if score >= 60 else sug[40] if score >= 40 else sug[20] if score >= 20 else sug[0]

    return {
        "score": score, "temperature": temp, "suggestion": suggestion,
        "zt_count": len(zt), "zbgc_count": len(zbgc), "blast_rate": round(blast_rate, 3),
        "max_board": max_board, "premium": round(avg_premium, 2), "win_rate": round(win_rate, 3),
    }


# ==================================================================
# Mock 持仓
# ==================================================================
class MockPortfolio:
    def __init__(self):
        self.positions = {
            "002528": {"name": "英飞拓", "volume": 2000, "entry_price": 11.20, "current_price": 12.88,
                       "stop_loss": 10.42, "stop_profit": 12.88, "strategy": "首板", "unrealized_pct": 0.15},
            "000977": {"name": "浪潮信息", "volume": 500, "entry_price": 36.50, "current_price": 34.20,
                       "stop_loss": 33.95, "stop_profit": 41.98, "strategy": "首板", "unrealized_pct": -0.063},
        }
        self.cash = 85000.0
        self.total_asset = self.cash + sum(p["volume"]*p["current_price"] for p in self.positions.values())

    def get_alerts(self):
        alerts = []
        for sym, p in self.positions.items():
            if p["current_price"] <= p["stop_loss"]:
                alerts.append({"symbol": sym, "name": p["name"], "type": "止损", "pnl_pct": p["unrealized_pct"]})
            elif p["current_price"] >= p["stop_profit"]:
                alerts.append({"symbol": sym, "name": p["name"], "type": "止盈", "pnl_pct": p["unrealized_pct"]})
        return alerts


# ==================================================================
# 主流程
# ==================================================================
def main():
    parser = argparse.ArgumentParser(description="AStock Mock 选股演示")
    parser.add_argument("--date", default=None, help="指定日期 YYYYMMDD")
    args = parser.parse_args()

    trade_date = args.date or datetime.now().strftime('%Y%m%d')
    display_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"

    print("=" * 60)
    print("AStock - 个人短线打板量化框架 [Mock 演示]")
    print("=" * 60)
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"数据日期: {display_date}")

    # 用日期作为随机种子，保证同一天结果一致
    seed = int(trade_date)
    data = MockData(seed=seed)

    # 1. 情绪
    print("\n" + "=" * 60)
    print("[1/4] 市场情绪分析")
    print("=" * 60)
    s = sentiment_analysis(data)
    print(f"情绪分数: {s['score']}/100  |  温度: {s['temperature']}")
    print(f"操作建议: {s['suggestion']}")
    print(f"涨停: {s['zt_count']}  |  炸板: {s['zbgc_count']}  |  炸板率: {s['blast_rate']:.1%}")
    print(f"连板高度: {s['max_board']}板  |  昨日溢价: {s['premium']}%  |  胜率: {s['win_rate']:.1%}")

    # 2. 选股
    print("\n" + "=" * 60)
    print("[2/4] 策略选股 & 交易计划")
    print("=" * 60)
    plans = generate_plans(data)
    print(f"共生成 {len(plans)} 条交易计划（Top 10）：\n")
    for i, p in enumerate(plans[:10], 1):
        print(f"{i:2d}. 【{p.action}】{p.name} ({p.symbol}) | {p.strategy}")
        print(f"     置信度: {p.confidence:.0%} | 建议仓位: {p.suggested_position:.0%}")
        print(f"     买入价: ¥{p.entry_price} | 止损: ¥{p.stop_loss} | 止盈: ¥{p.stop_profit}")
        print(f"     理由: {p.reason}")
        if p.risk_note:
            print(f"     ⚠️ 风险: {p.risk_note}")
        print()

    # 3. 持仓
    print("=" * 60)
    print("[3/4] 持仓 & 预警")
    print("=" * 60)
    pf = MockPortfolio()
    mv = sum(p["volume"]*p["current_price"] for p in pf.positions.values())
    print(f"总资产: ¥{pf.total_asset:,.2f}  现金: ¥{pf.cash:,.2f}  持仓市值: ¥{mv:,.2f}")
    print(f"\n{'代码':<8} {'名称':<8} {'数量':>8} {'成本':>8} {'现价':>8} {'盈亏':>8} {'止损':>8} {'止盈':>8}")
    for sym, p in pf.positions.items():
        pnl = p["unrealized_pct"]
        pnl_str = f"{pnl:+.2%}"
        print(f"{sym:<8} {p['name']:<8} {p['volume']:>8} {p['entry_price']:>8.2f} {p['current_price']:>8.2f} {pnl_str:>8} {p['stop_loss']:>8.2f} {p['stop_profit']:>8.2f}")

    alerts = pf.get_alerts()
    if alerts:
        print("\n⚠️ 预警:")
        for a in alerts:
            print(f"   [{a['type']}] {a['name']}({a['symbol']}) 盈亏 {a['pnl_pct']:+.2%}")
    else:
        print("\n✅ 无持仓预警")

    # 4. 推送模拟
    print("\n" + "=" * 60)
    print("[4/4] 推送内容预览")
    print("=" * 60)
    high = [p for p in plans if p.confidence >= 0.6][:5]
    title = f"AStock 交易计划 [{datetime.now().strftime('%m-%d')}]"
    lines = [f"## {title}\n"]
    for p in high:
        lines.append(f"**{p.action}** {p.name}({p.symbol}) | {p.strategy} | 置信度 {p.confidence:.0%}\n")
        lines.append(f"- 买入价 ¥{p.entry_price} / 止损 ¥{p.stop_loss} / 止盈 ¥{p.stop_profit}\n")
        lines.append(f"- 仓位 {p.suggested_position:.0%} | {p.reason}\n")
    content = "".join(lines)
    print(content)
    print("(实际运行时会通过钉钉/飞书 Webhook 推送以上内容)")

    print("\n" + "=" * 60)
    print("Demo 运行完毕。真实环境请执行: python -m astock.cli.main scan")
    print("=" * 60)


if __name__ == "__main__":
    main()
