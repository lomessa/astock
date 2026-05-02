"""
板块资金共振龙头策略
========================
核心逻辑：资金持续净流入的强势板块，其内部涨停龙头次日溢价概率显著高于随机涨停。
规则：
  1. 取近5日板块主力净流入 Top N 作为「强势板块」
  2. 筛选今日涨停池中，所属行业命中强势板块的个股
  3. 对「板块排名 + 个股质量」双因子打分
  4. 优先连板龙头，其次强势板块首板
"""
from datetime import datetime
from typing import List, Set

import pandas as pd
import numpy as np

from astock.strategy.base import StrategyBase
from astock.config import get_settings


def _is_st(name: str) -> bool:
    """判断是否 ST/*ST"""
    if pd.isna(name):
        return True
    return "ST" in str(name) or "退" in str(name)


def _match_industry(sector_names: List[str], industry: str) -> bool:
    """
    行业匹配：双向子串匹配。
    AKShare 板块排名和涨停池的行业名称可能略有差异（如 '光学光电子' vs '光学光电'），
    使用双向子串容错。
    """
    if pd.isna(industry) or not industry:
        return False
    ind = str(industry).strip()
    # 去除常见后缀，提高匹配率
    ind_clean = ind.replace("Ⅱ", "").replace("Ⅲ", "").replace("行业", "")
    for sec in sector_names:
        sec_clean = str(sec).strip().replace("Ⅱ", "").replace("Ⅲ", "").replace("行业", "")
        if ind_clean in sec_clean or sec_clean in ind_clean:
            return True
    return False


class SectorFlowLeaderStrategy(StrategyBase):
    """
    板块资金共振龙头策略
    =====================
    只做「资金在推」的板块的涨停股。
    """

    name = "板块资金共振"

    # 板块排名打分
    SECTOR_RANK_SCORE = {
        (1, 5): 30,
        (6, 10): 20,
        (11, 20): 10,
    }
    # 连板数打分
    BOARD_NUM_SCORE = {
        (2, 3): 25,
        (4, 5): 15,
        (1, 1): 10,
    }
    # 封单金额打分（亿）
    SEAL_AMOUNT_SCORE = {
        (5, float("inf")): 20,
        (2, 5): 15,
        (0.5, 2): 10,
    }
    # 流通市值打分（亿）
    FLOAT_MV_SCORE = {
        (0, 100): 15,
        (100, 200): 10,
    }
    # 换手率打分（%）
    TURNOVER_SCORE = {
        (5, 15): 10,
    }
    # 开板次数打分
    OPEN_TIMES_SCORE = {
        (0, 0): 10,
        (1, 1): 5,
    }

    def _get_sector_rank_score(self, rank: int) -> int:
        for (lo, hi), sc in self.SECTOR_RANK_SCORE.items():
            if lo <= rank <= hi:
                return sc
        return 0

    def _get_range_score(self, val: float, mapping: dict) -> int:
        if pd.isna(val):
            return 0
        for (lo, hi), sc in mapping.items():
            if lo <= val <= hi:
                return sc
        return 0

    def select_candidates(self, data, trade_date: str) -> pd.DataFrame:
        cfg = get_settings().strategy
        if not getattr(cfg, "enable_sector_flow", True):
            return pd.DataFrame()

        # 1. 获取5日板块主力净流入排名
        df_sector = data.get_sector_hot(trade_date)
        if df_sector.empty or "sector_name" not in df_sector.columns:
            print("[SectorFlow] 获取板块资金流向失败，跳过本策略")
            return pd.DataFrame()

        top_n = getattr(cfg, "sector_flow_top_n", 20)
        df_sector = df_sector.head(top_n).reset_index(drop=True)
        df_sector["sector_rank"] = df_sector.index + 1  # 1-based rank
        sector_names = df_sector["sector_name"].tolist()
        print(f"[SectorFlow] 强势板块 Top{top_n}: {sector_names[:10]}...")

        # --- 爆发首日模式 ---
        burst_mode = getattr(cfg, "sector_flow_burst_mode", True)
        lookback = getattr(cfg, "sector_flow_lookback_days", 3)
        new_entry_bonus = getattr(cfg, "sector_flow_new_entry_bonus", 0.15)
        new_sectors = set()
        history_ready = False

        if burst_mode:
            try:
                hist = data.get_sector_hot_history(days=lookback + 1)
                if not hist.empty and len(hist["rank_date"].unique()) >= 2:
                    # 收集历史上每天的Top 5板块
                    hist_top5 = set()
                    for d in hist["rank_date"].unique():
                        day_df = hist[hist["rank_date"] == d]
                        top5 = day_df.head(5)["sector_name"].tolist()
                        hist_top5.update(top5)
                    # 今日Top 5中，哪些是新进入的
                    today_top5 = set(df_sector.head(5)["sector_name"].tolist())
                    new_sectors = today_top5 - hist_top5
                    history_ready = True
                    if new_sectors:
                        print(f"[SectorFlow] 🚀 爆发首日板块: {list(new_sectors)}")
                    else:
                        print(f"[SectorFlow] 今日Top5板块均为持续热点，无新爆发")
                else:
                    print(f"[SectorFlow] 本地历史板块数据不足（需至少2天），降级为常规模式")
            except Exception as e:
                print(f"[SectorFlow] 读取板块历史失败，降级为常规模式: {e}")

        # 2. 获取今日涨停池
        df_zt = data.get_zt_pool(trade_date)
        if df_zt.empty:
            return pd.DataFrame()

        # 3. 行业匹配：只保留属于强势板块的涨停股
        if "industry" not in df_zt.columns:
            print("[SectorFlow] 涨停池缺少行业信息，跳过")
            return pd.DataFrame()

        df_zt["sector_matched"] = df_zt["industry"].apply(
            lambda ind: _match_industry(sector_names, ind)
        )
        df = df_zt[df_zt["sector_matched"]].copy()
        if df.empty:
            print("[SectorFlow] 今日涨停股无板块共振信号")
            return pd.DataFrame()

        # 记录每个股票匹配到的板块排名（取最佳排名）
        def _best_rank(ind):
            best = 999
            for _, row in df_sector.iterrows():
                sec = str(row["sector_name"]).strip().replace("Ⅱ", "").replace("Ⅲ", "").replace("行业", "")
                ind_clean = str(ind).strip().replace("Ⅱ", "").replace("Ⅲ", "").replace("行业", "")
                if ind_clean in sec or sec in ind_clean:
                    best = min(best, int(row["sector_rank"]))
            return best

        df["sector_rank"] = df["industry"].apply(_best_rank)

        # 标记是否属于"新爆发"板块
        def _is_new_burst(ind):
            if not new_sectors:
                return False
            for sec in new_sectors:
                sec_clean = str(sec).strip().replace("Ⅱ", "").replace("Ⅲ", "").replace("行业", "")
                ind_clean = str(ind).strip().replace("Ⅱ", "").replace("Ⅲ", "").replace("行业", "")
                if ind_clean in sec_clean or sec_clean in ind_clean:
                    return True
            return False

        df["is_new_burst"] = df["industry"].apply(_is_new_burst)

        # 4. 基础过滤
        if "name" in df.columns:
            df = df[~df["name"].apply(_is_st)]
        if "seal_amount" in df.columns:
            df = df[df["seal_amount"] >= 0.3]
        if "open_times" in df.columns:
            df = df[df["open_times"] <= 2]

        if df.empty:
            return pd.DataFrame()

        # 5. 多因子打分
        max_score = 110  # 基础满分

        def _calc_total_score(row: pd.Series) -> float:
            score = 0.0
            # 板块排名
            score += self._get_sector_rank_score(int(row.get("sector_rank", 999)))
            # 连板数
            bn = row.get("board_num", 1)
            if pd.notna(bn):
                bn = int(bn)
                if bn >= 6:
                    score += 5
                else:
                    score += self._get_range_score(bn, self.BOARD_NUM_SCORE)
            # 封单金额
            score += self._get_range_score(row.get("seal_amount", 0), self.SEAL_AMOUNT_SCORE)
            # 流通市值
            score += self._get_range_score(row.get("float_mv", 999), self.FLOAT_MV_SCORE)
            # 换手率
            score += self._get_range_score(row.get("turnover", 0), self.TURNOVER_SCORE)
            # 开板次数
            score += self._get_range_score(row.get("open_times", 0), self.OPEN_TIMES_SCORE)
            # 爆发首日额外加分
            if row.get("is_new_burst"):
                score += int(new_entry_bonus * max_score)  # 默认 +16分
            return score

        df["raw_score"] = df.apply(_calc_total_score, axis=1)
        # 如果开启了爆发首日模式且有新板块，扩大满分以容纳额外加分
        effective_max = max_score + (int(new_entry_bonus * max_score) if df["is_new_burst"].any() else 0)
        df["confidence"] = (df["raw_score"] / effective_max).clip(upper=0.99).round(3)
        df["score"] = df["confidence"]

        # 构建 reason
        def _build_reason(r):
            parts = [f"板块资金共振：所属板块5日净流入排名第{r['sector_rank']}位"]
            if r.get("is_new_burst"):
                parts[-1] = f"🚀 板块爆发首日：所属「{r.get('industry','')}」5日净流入第{r['sector_rank']}位"
            if pd.notna(r.get("board_num")) and r["board_num"] > 1:
                parts.append(f"{int(r['board_num'])}连板")
            else:
                parts.append("首板")
            if pd.notna(r.get("seal_amount")):
                parts.append(f"封单{r['seal_amount']:.2f}亿")
            if pd.notna(r.get("float_mv")):
                parts.append(f"流通市值{r['float_mv']:.1f}亿")
            return "，".join(parts)

        df["reason"] = df.apply(_build_reason, axis=1)
        df["strategy"] = self.name

        cols = ["symbol", "name", "score", "confidence", "reason", "strategy",
                "close", "float_mv", "seal_amount", "turnover", "open_times",
                "board_num", "industry", "sector_rank", "is_new_burst"]
        cols = [c for c in cols if c in df.columns]
        return df[cols].sort_values("score", ascending=False)

class SectorFlowSurgeStrategy(StrategyBase):
    """
    板块异动低吸策略
    =================
    核心逻辑：强势板块内部，当日出现明显资金异动（涨幅 2%-7%）但尚未涨停的个股，
    存在次日补涨或被板块情绪带动的预期。
    与「板块资金共振龙头」形成互补：一个做龙头，一个做跟风/补涨。
    """

    name = "板块异动低吸"

    def select_candidates(self, data, trade_date: str) -> pd.DataFrame:
        cfg = get_settings().strategy
        if not getattr(cfg, "enable_sector_flow_surge", True):
            return pd.DataFrame()

        # 1. 获取5日强势板块 Top N（只关注最前排，减少请求量）
        df_sector = data.get_sector_hot()
        if df_sector.empty or "名称" not in df_sector.columns:
            print("[SectorFlowSurge] 获取板块资金流向失败，跳过")
            return pd.DataFrame()

        top_n = getattr(cfg, "sector_flow_surge_top_n", 8)
        df_sector = df_sector.head(top_n).reset_index(drop=True)
        df_sector["sector_rank"] = df_sector.index + 1
        print(f"[SectorFlowSurge] 关注强势板块 Top{top_n}: {df_sector['名称'].tolist()}")

        # 2. 获取全市场快照（用于市值、名称、ST、量比、成交额判断）
        df_spot = data.get_stock_info()
        if df_spot.empty:
            return pd.DataFrame()
        spot_cols = ["symbol", "float_mv", "name", "volume_ratio", "amount"]
        df_spot = df_spot[[c for c in spot_cols if c in df_spot.columns]].copy()

        # 3. 遍历强势板块，获取成分股（只取异动股）
        all_cons = []
        for _, row in df_sector.iterrows():
            sector_name = row["名称"]
            rank = int(row["sector_rank"])
            try:
                df_cons = data.get_sector_cons(sector_name)
                if df_cons.empty or "change_pct" not in df_cons.columns:
                    continue
                # 只保留有异动但未涨停的（节省内存）
                df_cons = df_cons[
                    (df_cons["change_pct"] >= 2.0) & (df_cons["change_pct"] <= 7.0)
                ].copy()
                if df_cons.empty:
                    continue
                df_cons["sector_name"] = sector_name
                df_cons["sector_rank"] = rank
                all_cons.append(df_cons)
            except Exception as e:
                print(f"[SectorFlowSurge] 板块「{sector_name}」成分股获取失败: {e}")
                continue

        if not all_cons:
            print("[SectorFlowSurge] 无板块异动标的")
            return pd.DataFrame()

        df = pd.concat(all_cons, ignore_index=True)

        # 去重：同一只股票属于多个板块时，取排名最靠前的
        df = df.sort_values("sector_rank").drop_duplicates(subset=["symbol"], keep="first")

        # 4. 合并市值与名称
        if "name" in df.columns and "name" in df_spot.columns:
            df = df.drop(columns=["name"])
        df = df.merge(df_spot, on="symbol", how="left")

        # 5. 基础过滤（严格筛选，减少候选数量）
        if "name" in df.columns:
            df = df[~df["name"].apply(_is_st)]
        # 涨幅：3%-6%（有异动但未过热）
        df = df[(df["change_pct"] >= 3.0) & (df["change_pct"] <= 6.0)]
        # 换手
        if "turnover" in df.columns:
            df = df[df["turnover"] >= 3.0]
        # 市值
        if "float_mv" in df.columns:
            df = df[(df["float_mv"] >= 20) & (df["float_mv"] <= 300)]
        # 量比：确认资金真实流入（从spot merge进来）
        if "volume_ratio" in df.columns:
            df = df[df["volume_ratio"] >= 1.5]
        # 成交额：至少1亿（排除流动性差的）
        if "amount" in df.columns:
            df = df[df["amount"] >= 1.0]

        if df.empty:
            print("[SectorFlowSurge] 过滤后无符合条件的标的")
            return pd.DataFrame()

        print(f"[SectorFlowSurge] 过滤后剩余 {len(df)} 只候选股")

        # 6. 多因子打分（满分100，让分布更分散）
        def _calc_score(r: pd.Series) -> float:
            score = 0.0
            # 板块排名（25分）
            sr = int(r.get("sector_rank", 99))
            if sr <= 3: score += 25
            elif sr <= 5: score += 20
            elif sr <= 8: score += 15
            else: score += 10
            # 涨幅适中：3.5%-5% 最佳（25分）
            cp = float(r.get("change_pct", 0))
            if 3.5 <= cp <= 5.0: score += 25
            elif 3.0 <= cp < 3.5 or 5.0 < cp <= 6.0: score += 18
            else: score += 10
            # 换手率：3%-10%（15分）
            turn = float(r.get("turnover", 0) or 0)
            if 3.0 <= turn <= 10.0: score += 15
            elif turn >= 2.0: score += 10
            # 流通市值：<=80亿弹性最大（15分）
            mv = float(r.get("float_mv", 999) or 999)
            if mv <= 80: score += 15
            elif mv <= 150: score += 10
            else: score += 5
            # 量比：>=2.0 确认放量（10分）
            vr = float(r.get("volume_ratio", 0) or 0)
            if vr >= 2.0: score += 10
            elif vr >= 1.5: score += 5
            return score

        max_score = 25 + 25 + 15 + 15 + 10  # 90
        df["raw_score"] = df.apply(_calc_score, axis=1)
        df["confidence"] = (df["raw_score"] / max_score).clip(upper=0.99).round(3)
        df["score"] = df["confidence"]

        # 7. 构建 reason
        def _build_reason(r):
            parts = [
                f"板块异动低吸：所属「{r['sector_name']}」5日净流入第{int(r['sector_rank'])}位",
                f"当日异动+{r['change_pct']:.2f}%",
            ]
            if pd.notna(r.get("turnover")):
                parts.append(f"换手{r['turnover']:.1f}%")
            if pd.notna(r.get("float_mv")):
                parts.append(f"流通市值{r['float_mv']:.1f}亿")
            if pd.notna(r.get("volume_ratio")):
                parts.append(f"量比{r['volume_ratio']:.1f}")
            return "，".join(parts)

        df["reason"] = df.apply(_build_reason, axis=1)
        df["strategy"] = self.name
        df["close"] = df["price"]

        cols = [
            "symbol", "name", "score", "confidence", "reason", "strategy",
            "close", "float_mv", "turnover", "change_pct", "volume_ratio",
            "sector_name", "sector_rank",
        ]
        cols = [c for c in cols if c in df.columns]
        # 只保留Top 10，聚焦核心标的
        return df[cols].sort_values("score", ascending=False).head(10)
