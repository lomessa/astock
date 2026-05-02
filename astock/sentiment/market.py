"""
市场情绪分析
============
用于判断当天/近期是否适合出手，以及仓位控制。
"""
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

import pandas as pd
import numpy as np

from astock.data.client import DataClient


class SentimentAnalyzer:
    """市场情绪分析器"""

    def __init__(self, data: Optional[DataClient] = None):
        self.data = data or DataClient()

    def analyze(self, trade_date: Optional[str] = None) -> Dict[str, Any]:
        """
        综合分析市场情绪

        Returns
        -------
        dict
            包含涨停数、跌停数、连板高度、炸板率、昨日涨停溢价等
        """
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")

        result = {
            "trade_date": trade_date,
            "score": 50,  # 0-100，越高越好
            "temperature": "温和",  # 冰冷/低温/温和/高温/沸腾
            "suggestion": "观望",
        }

        # 1. 涨停数据
        df_zt = self.data.get_zt_pool(trade_date)
        zt_count = len(df_zt) if not df_zt.empty else 0
        result["zt_count"] = zt_count

        # 2. 连板高度
        max_board = 0
        if not df_zt.empty and "board_num" in df_zt.columns:
            max_board = int(df_zt["board_num"].max()) if pd.notna(df_zt["board_num"].max()) else 0
        result["max_board"] = max_board

        # 3. 炸板率（炸板数 / (涨停数 + 炸板数)）
        df_zbgc = self.data.get_zt_pool_zbgc(trade_date)
        zbgc_count = len(df_zbgc) if not df_zbgc.empty else 0
        blast_rate = zbgc_count / (zt_count + zbgc_count + 1e-6)
        result["blast_rate"] = round(blast_rate, 3)
        result["zbgc_count"] = zbgc_count

        # 4. 昨日涨停今日溢价（赚钱效应）
        yest = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        df_prev = self.data.get_zt_pool_previous(trade_date)
        premium = 0.0
        if not df_prev.empty and "change_pct" in df_prev.columns:
            premium = round(df_prev["change_pct"].mean(), 2)
        result["premium"] = premium
        win_rate = 0.0
        if not df_prev.empty and "change_pct" in df_prev.columns:
            win_rate = round((df_prev["change_pct"] > 0).sum() / len(df_prev), 3)
        result["win_rate"] = win_rate

        # 5. 指数位置（上证 5/10/20 日均线）
        index_df = self.data.get_index_daily("000001")
        index_trend = "震荡"
        if not index_df.empty and len(index_df) >= 20:
            latest = index_df.iloc[-1]["close"]
            ma5 = index_df["close"].rolling(5).mean().iloc[-1]
            ma10 = index_df["close"].rolling(10).mean().iloc[-1]
            ma20 = index_df["close"].rolling(20).mean().iloc[-1]
            result["index_close"] = round(latest, 2)
            result["ma5"] = round(ma5, 2)
            result["ma10"] = round(ma10, 2)
            result["ma20"] = round(ma20, 2)
            if latest > ma5 > ma10 > ma20:
                index_trend = "多头排列"
            elif latest < ma5 < ma10 < ma20:
                index_trend = "空头排列"
            else:
                index_trend = "震荡"
        result["index_trend"] = index_trend

        # 6. 综合打分 & 温度 & 建议
        score = 50
        # 涨停数加分
        if zt_count >= 100:
            score += 15
        elif zt_count >= 60:
            score += 10
        elif zt_count >= 30:
            score += 5
        else:
            score -= 10

        # 连板高度
        if max_board >= 6:
            score += 10
        elif max_board >= 4:
            score += 5
        elif max_board >= 2:
            score += 0
        else:
            score -= 15

        # 炸板率
        if blast_rate < 0.15:
            score += 10
        elif blast_rate < 0.25:
            score += 5
        elif blast_rate < 0.35:
            score += 0
        else:
            score -= 15

        # 溢价
        if premium > 3:
            score += 10
        elif premium > 1:
            score += 5
        elif premium > 0:
            score += 0
        else:
            score -= 10

        # 指数趋势
        if index_trend == "多头排列":
            score += 5
        elif index_trend == "空头排列":
            score -= 10

        score = max(0, min(100, score))
        result["score"] = score

        if score >= 80:
            result["temperature"] = "沸腾"
            result["suggestion"] = "积极出手，重仓参与核心"
        elif score >= 60:
            result["temperature"] = "高温"
            result["suggestion"] = "可出手，控制仓位"
        elif score >= 40:
            result["temperature"] = "温和"
            result["suggestion"] = "轻仓试错或观望"
        elif score >= 20:
            result["temperature"] = "低温"
            result["suggestion"] = "观望为主，减少操作"
        else:
            result["temperature"] = "冰冷"
            result["suggestion"] = "空仓休息"

        return result

    def format_report(self, result: Dict[str, Any]) -> str:
        """格式化情绪报告"""
        lines = [
            f"🔥 市场情绪报告 [{result['trade_date']}]",
            f"情绪分数: {result['score']}/100  |  温度: {result['temperature']}",
            f"操作建议: {result['suggestion']}",
            "-" * 40,
            f"涨停家数: {result['zt_count']}  |  炸板数: {result.get('zbgc_count', 0)}  |  炸板率: {result['blast_rate']:.1%}",
            f"连板高度: {result['max_board']}板  |  昨日涨停溢价: {result['premium']}%  |  胜率: {result['win_rate']:.1%}",
        ]
        if "index_close" in result:
            lines.append(
                f"上证指数: {result['index_close']} (MA5:{result['ma5']} MA10:{result['ma10']} MA20:{result['ma20']}) [{result['index_trend']}]"
            )
        return "\n".join(lines)
