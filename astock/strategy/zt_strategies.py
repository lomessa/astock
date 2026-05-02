"""
打板策略集合
================
1. 首板策略（FirstBoard）
2. 连板接力（ContinuousBoard）
3. 炸板回封（Reseal）
4. 竞价超预期（AuctionSurprise）
5. 强势股回调（StrongStockPullback）
"""
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import numpy as np

from astock.strategy.base import StrategyBase
from astock.config import get_settings


# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------
def _is_st(name: str) -> bool:
    """判断是否 ST/*ST"""
    if pd.isna(name):
        return True
    return "ST" in str(name) or "退" in str(name)


def _calc_confidence(row: pd.Series, weights: dict) -> float:
    """基于多因子计算置信度（0-1）"""
    score = 0.0
    total = 0.0
    for key, w in weights.items():
        if key in row and pd.notna(row[key]):
            # 简单归一化到 0-1
            val = float(row[key])
            if key in ["float_mv", "amount"]:
                # 市值越小越好，成交额适中越好
                score += w * max(0, 1 - val / 200)
            elif key == "seal_amount":
                score += w * min(1, val / 5)
            elif key == "open_times":
                score += w * max(0, 1 - val / 5)
            elif key == "turnover":
                # 换手 5-15 最佳
                score += w * (1 - abs(val - 10) / 20)
            elif key == "board_num":
                # 连板数 2-3 最佳
                score += w * max(0, 1 - abs(val - 2.5) / 3)
            else:
                score += w * min(1, max(0, val))
            total += w
    return round(score / total, 3) if total > 0 else 0.5


# ------------------------------------------------------------------
# 1. 首板策略
# ------------------------------------------------------------------
class FirstBoardStrategy(StrategyBase):
    """
    首板策略
    ========
    逻辑：昨日未涨停，今日首次涨停，封板质量好。
    加分项：小流通市值（<200亿）、换手充分（3%-30%）、非ST、题材热点。
    """

    name = "首板"

    def select_candidates(self, data, trade_date: str) -> pd.DataFrame:
        cfg = get_settings().strategy
        if not cfg.enable_first_board:
            return pd.DataFrame()

        # 今日涨停池
        df_today = data.get_zt_pool(trade_date)
        if df_today.empty:
            return pd.DataFrame()

        # 昨日涨停池（排除连板）
        yest = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        df_yest = data.get_zt_pool(yest)
        yest_symbols = set(df_yest["symbol"].tolist()) if not df_yest.empty else set()

        # 筛选首板
        df = df_today[df_today["board_num"] == 1].copy()
        if df.empty:
            return pd.DataFrame()

        # 排除昨日已涨停（确保是首板）
        df = df[~df["symbol"].isin(yest_symbols)]

        # 排除 ST
        if "name" in df.columns:
            df = df[~df["name"].apply(_is_st)]

        # 流通市值过滤
        if "float_mv" in df.columns:
            df = df[df["float_mv"] <= cfg.fb_max_float_mv]

        # 换手率过滤
        if "turnover" in df.columns:
            df = df[(df["turnover"] >= cfg.fb_min_turnover) & (df["turnover"] <= cfg.fb_max_turnover)]

        # 封板资金过滤（至少 3000 万）
        if "seal_amount" in df.columns:
            df = df[df["seal_amount"] >= 0.3]

        # 开板次数越少越好
        if "open_times" in df.columns:
            df = df[df["open_times"] <= 2]

        if df.empty:
            return pd.DataFrame()

        # 计算置信度
        weights = {
            "float_mv": 0.25,
            "seal_amount": 0.30,
            "open_times": 0.25,
            "turnover": 0.20,
        }
        df["confidence"] = df.apply(lambda r: _calc_confidence(r, weights), axis=1)
        df["score"] = df["confidence"]  # 简化
        df["reason"] = (
            "首板突破：流通市值" + df["float_mv"].round(1).astype(str) + "亿，"
            + "封单" + df["seal_amount"].round(2).astype(str) + "亿，"
            + "换手" + df["turnover"].round(1).astype(str) + "%"
        )
        df["strategy"] = self.name
        return df[["symbol", "name", "score", "confidence", "reason", "strategy",
                   "close", "float_mv", "seal_amount", "turnover", "open_times", "industry"]].sort_values("score", ascending=False)


# ------------------------------------------------------------------
# 2. 连板接力策略
# ------------------------------------------------------------------
class ContinuousBoardStrategy(StrategyBase):
    """
    连板接力策略
    ============
    逻辑：接力市场核心高标，但不做最高标（防止天地板）。
    优选 2-4 连板，封单金额大、开板次数少、板块效应强。
    """

    name = "连板接力"

    def select_candidates(self, data, trade_date: str) -> pd.DataFrame:
        cfg = get_settings().strategy
        if not cfg.enable_continuous_board:
            return pd.DataFrame()

        df = data.get_zt_pool(trade_date)
        if df.empty or "board_num" not in df.columns:
            return pd.DataFrame()

        # 连板数过滤
        df = df[(df["board_num"] >= cfg.cb_min_board) & (df["board_num"] <= cfg.cb_max_board)]

        # 排除 ST
        if "name" in df.columns:
            df = df[~df["name"].apply(_is_st)]

        # 封单金额
        if "seal_amount" in df.columns:
            df = df[df["seal_amount"] >= cfg.cb_min_amount]

        # 开板次数
        if "open_times" in df.columns:
            df = df[df["open_times"] <= 1]

        if df.empty:
            return pd.DataFrame()

        weights = {
            "board_num": 0.30,
            "seal_amount": 0.35,
            "open_times": 0.20,
            "float_mv": 0.15,
        }
        df["confidence"] = df.apply(lambda r: _calc_confidence(r, weights), axis=1)
        df["score"] = df["confidence"]
        df["reason"] = (
            "连板接力：" + df["board_num"].astype(int).astype(str) + "连板，"
            + "封单" + df["seal_amount"].round(2).astype(str) + "亿"
        )
        df["strategy"] = self.name
        return df[["symbol", "name", "score", "confidence", "reason", "strategy",
                   "close", "float_mv", "seal_amount", "turnover", "open_times",
                   "board_num", "industry"]].sort_values("score", ascending=False)


# ------------------------------------------------------------------
# 3. 炸板回封策略
# ------------------------------------------------------------------
class ResealStrategy(StrategyBase):
    """
    炸板回封策略
    ============
    逻辑：盘中炸板后再次封板，筹码交换充分，后续抛压小。
    条件：开板次数 <=3，回封时间早（如 10:30 前），回封时封单大。
    注：此策略主要盘中使用，盘前只能做预备筛选。
    """

    name = "炸板回封"

    def select_candidates(self, data, trade_date: str) -> pd.DataFrame:
        cfg = get_settings().strategy
        if not cfg.enable_reseal:
            return pd.DataFrame()

        # 从炸板池获取
        df = data.get_zt_pool_zbgc(trade_date)
        if df.empty:
            return pd.DataFrame()

        # 排除 ST
        if "name" in df.columns:
            df = df[~df["name"].apply(_is_st)]

        # 过滤条件（炸板池默认已炸板，需结合回封时间判断）
        # 如果 last_seal_time 存在，过滤早回封
        if "last_seal_time" in df.columns:
            deadline = datetime.strptime(cfg.reseal_min_reseal_time, "%H:%M").time()
            df["last_time"] = pd.to_datetime(df["last_seal_time"], format="%H%M%S", errors="coerce").dt.time
            df = df[df["last_time"] <= deadline]

        if "open_times" in df.columns:
            df = df[df["open_times"] <= cfg.reseal_max_open_times]

        if df.empty:
            return pd.DataFrame()

        # 炸板池可能没有 seal_amount，动态构建权重
        weights = {"open_times": 0.50, "float_mv": 0.25}
        if "seal_amount" in df.columns:
            weights["seal_amount"] = 0.25
        df["confidence"] = df.apply(lambda r: _calc_confidence(r, weights), axis=1)
        df["score"] = df["confidence"]

        reason_base = "炸板回封：开板" + df["open_times"].fillna(0).astype(int).astype(str) + "次"
        if "seal_amount" in df.columns:
            df["reason"] = reason_base + "，回封后封单" + df["seal_amount"].fillna(0).astype(str) + "亿"
        else:
            df["reason"] = reason_base
        df["strategy"] = self.name

        cols = ["symbol", "name", "score", "confidence", "reason", "strategy",
                "close", "float_mv", "open_times", "industry"]
        if "seal_amount" in df.columns:
            cols.insert(-1, "seal_amount")
        return df[cols].sort_values("score", ascending=False)

    def intraday_alert(self, data, trade_date: str) -> pd.DataFrame:
        """盘中实时提醒炸板回封机会"""
        return self.select_candidates(data, trade_date)


# ------------------------------------------------------------------
# 4. 强势股回调策略
# ------------------------------------------------------------------
class StrongStockPullbackStrategy(StrategyBase):
    """
    强势股回调策略（短线最稳的一种）
    =================================
    核心：只做强者（60日涨幅>20%），不做弱者；只做回调，不做追高。
    规则：
      - 趋势行业：AI、创新药、半导体、通信、计算机、传媒、电子、军工等
      - 从近20日高点跌 15%-30% 再进
      - 反弹 10%-20% 就走
      - 严格止损 5%-8%
    """

    name = "强势股回调"

    # 趋势向上的热点行业关键词（用于过滤）
    TREND_INDUSTRY_KEYWORDS = [
        "人工智能", "AIGC", "ChatGPT", "AI", "算力", "大模型",
        "半导体", "芯片", "集成电路", "光刻", "存储",
        "创新药", "生物医药", "化学制药", "医疗器械", "CXO",
        "通信设备", "计算机", "软件", "IT服务", "互联网",
        "传媒", "游戏", "影视", "广告",
        "电子", "消费电子", "光学光电子", "元件",
        "国防军工", "航天", "船舶", "军工",
        "新能源", "光伏", "储能", "锂电", "电池",
        "机器人", "自动化", "专用设备",
        "汽车", "汽车零部件",
        "有色金属", "稀土", "磁性",
    ]

    def select_candidates(self, data, trade_date: str) -> pd.DataFrame:
        cfg = get_settings().strategy
        if not cfg.enable_pullback:
            return pd.DataFrame()

        # 1. 获取全市场快照
        df = data.get_stock_info()
        if df.empty:
            return pd.DataFrame()

        # 2. 基础过滤：排除ST、价格、市值、60日涨幅（确认强势）
        if "name" in df.columns:
            df = df[~df["name"].apply(_is_st)]
        if "close" in df.columns:
            df = df[(df["close"] >= 3.0) & (df["close"] <= 200.0)]
        if "float_mv" in df.columns:
            df = df[(df["float_mv"] >= cfg.pb_min_market_cap) & (df["float_mv"] <= cfg.pb_max_market_cap)]
        if "change_60d" in df.columns:
            df = df[df["change_60d"] >= cfg.pb_min_60d_change]
        else:
            # 没有60日涨幅数据则无法判断强势，直接返回
            return pd.DataFrame()

        if df.empty:
            return pd.DataFrame()

        # 3. 按60日涨幅排序，取Top N 强势股做深入分析
        df = df.sort_values("change_60d", ascending=False).head(cfg.pb_max_candidates)

        # 4. 逐个获取历史K线，计算从近20日高点回落幅度
        results = []
        for _, row in df.iterrows():
            symbol = str(row.get("symbol", ""))
            if not symbol:
                continue
            try:
                kline = data.get_daily(symbol, start_date=None, end_date=trade_date)
                if kline.empty or len(kline) < 20:
                    continue
                # 近20日高点（不含今日）
                high_20d = kline["high"].iloc[-20:-1].max()
                close = float(kline["close"].iloc[-1])
                if pd.isna(high_20d) or high_20d <= 0:
                    continue
                pullback = (high_20d - close) / high_20d * 100.0
                if cfg.pb_min_pullback <= pullback <= cfg.pb_max_pullback:
                    results.append({
                        "symbol": symbol,
                        "name": row.get("name", ""),
                        "close": close,
                        "high_20d": round(high_20d, 2),
                        "pullback": round(pullback, 2),
                        "change_60d": row.get("change_60d"),
                        "float_mv": row.get("float_mv"),
                        "turnover": row.get("turnover"),
                        "industry": row.get("industry", ""),
                    })
            except Exception:
                continue

        if not results:
            return pd.DataFrame()

        df_result = pd.DataFrame(results)

        # 5. 行业过滤（如果有行业信息）
        if "industry" in df_result.columns and cfg.pb_enable_industry_filter:
            df_result["industry_match"] = df_result["industry"].apply(
                lambda x: any(kw in str(x) for kw in self.TREND_INDUSTRY_KEYWORDS)
            )
            df_result = df_result[df_result["industry_match"]]
            df_result = df_result.drop(columns=["industry_match"])

        if df_result.empty:
            return pd.DataFrame()

        # 6. 计算置信度：跌幅适中（20%-25%最佳）、60日涨幅大、换手适中加分
        def _calc_pb_confidence(r):
            score = 0.0
            # 跌幅适中：20-25% 最佳
            pb = r["pullback"]
            if 20 <= pb <= 25:
                score += 0.40
            elif 15 <= pb < 20 or 25 < pb <= 30:
                score += 0.25
            # 60日涨幅大
            c60 = r["change_60d"]
            score += min(0.30, c60 / 200)
            # 换手适中（3-15%）
            turn = r.get("turnover", 0) or 0
            if 3 <= turn <= 15:
                score += 0.15
            # 市值适中（<200亿）
            mv = r.get("float_mv", 999) or 999
            if mv <= 200:
                score += 0.15
            return min(0.99, round(score, 3))

        df_result["confidence"] = df_result.apply(_calc_pb_confidence, axis=1)
        df_result["score"] = df_result["confidence"]
        df_result["reason"] = (
            "强势股回调：60日涨幅" + df_result["change_60d"].astype(str) + "%，"
            + "近20日高点" + df_result["high_20d"].astype(str) + "，"
            + "当前回落" + df_result["pullback"].astype(str) + "%"
        )
        df_result["strategy"] = self.name

        # 7. 自定义止盈止损（覆盖全局默认值）
        df_result["stop_loss"] = round(df_result["close"] * (1 - cfg.pb_stop_loss), 2)
        df_result["stop_profit"] = round(df_result["close"] * (1 + cfg.pb_stop_profit), 2)
        df_result["dynamic_stop"] = round(df_result["close"] * (1 + cfg.pb_dynamic_stop), 2)

        cols = ["symbol", "name", "score", "confidence", "reason", "strategy",
                "close", "float_mv", "turnover", "pullback", "change_60d", "industry"]
        return df_result[cols].sort_values("score", ascending=False)


# ------------------------------------------------------------------
# 5. 竞价超预期策略
# ------------------------------------------------------------------
class AuctionStrategy(StrategyBase):
    """
    竞价超预期策略
    ==============
    逻辑：昨日烂板/炸板/大分歧，今日竞价高开，量能放大，说明资金抢筹。
    数据限制：AKShare 免费接口获取竞价数据较困难，本策略基于前一日特征 + 当日开盘涨幅近似。
    实际使用建议接入券商 Level-2 竞价数据。
    """

    name = "竞价超预期"

    def select_candidates(self, data, trade_date: str) -> pd.DataFrame:
        cfg = get_settings().strategy
        if not cfg.enable_auction:
            return pd.DataFrame()

        # 获取昨日涨停今日表现（昨日烂板今日高开）
        df = data.get_zt_pool_previous(trade_date)
        if df.empty:
            return pd.DataFrame()

        # 排除 ST
        if "name" in df.columns:
            df = df[~df["name"].apply(_is_st)]

        # 高开过滤（> 3%）
        if "change_pct" in df.columns:
            df = df[df["change_pct"] > 3.0]

        # 流通市值
        if "float_mv" in df.columns:
            df = df[df["float_mv"] <= 200]

        if df.empty:
            return pd.DataFrame()

        df["confidence"] = 0.6  # 默认中等置信度，因缺少真实竞价量
        df["score"] = df["change_pct"] / 20  # 粗略打分
        df["reason"] = "竞价超预期：昨日烂板今日高开 " + df["change_pct"].round(2).astype(str) + "%"
        df["strategy"] = self.name
        return df[["symbol", "name", "score", "confidence", "reason", "strategy",
                   "close", "float_mv", "change_pct", "industry"]].sort_values("score", ascending=False)
