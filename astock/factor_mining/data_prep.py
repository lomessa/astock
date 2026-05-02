"""
数据准备
========
从本地 SQLite 批量读取历史数据，构建宽格式面板数据。
"""
import sqlite3
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

from astock.config import get_settings


def prepare_data_from_db(end_date: str, lookback_days: int = 60, forward_period: int = 3):
    """
    准备宽格式面板数据

    Parameters
    ----------
    end_date : str
        截止日期，格式 YYYYMMDD
    lookback_days : int
        回看交易日数量（实际 SQL 会多取 30 天用于 rolling）
    forward_period : int
        预测未来 N 日收益率（默认 3，即 t+3）

    Returns
    -------
    data_dict : dict[str, pd.DataFrame]
        字段名 -> 宽格式 DataFrame（index=date, columns=symbol）
    label_df : pd.DataFrame
        未来 forward_period 日收盘收益率（index=date, columns=symbol）
    """
    if forward_period < 1:
        forward_period = 1
    cfg = get_settings().data
    conn = sqlite3.connect(cfg.db_path)

    end_dt = pd.to_datetime(end_date)
    start_dt = end_dt - timedelta(days=lookback_days + 30)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")

    query = f"""
        SELECT * FROM daily
        WHERE date >= '{start_str}' AND date <= '{end_str}'
        ORDER BY date, symbol
    """
    panel = pd.read_sql_query(query, conn, parse_dates=["date"])
    conn.close()

    if panel.empty:
        print("[WARN] 本地数据库无数据，请先运行数据同步或等待 AKShare 缓存写入。")
        return None, None

    # 过滤掉数据太少的股票（至少 lookback_days/2 条）
    min_rows = max(lookback_days // 2, 10)
    sym_counts = panel["symbol"].value_counts()
    valid_syms = sym_counts[sym_counts >= min_rows].index
    panel = panel[panel["symbol"].isin(valid_syms)]

    if panel.empty:
        print("[WARN] 过滤后无足够数据。")
        return None, None

    # 转为宽格式
    fields = ["open", "high", "low", "close", "volume", "amount", "turnover"]
    data_dict = {}
    for f in fields:
        if f in panel.columns:
            wide = panel.pivot(index="date", columns="symbol", values=f)
            data_dict[f.upper()] = wide

    # 计算 label：未来 forward_period 日收盘收益率
    close_wide = data_dict.get("CLOSE")
    if close_wide is None or close_wide.empty:
        return None, None

    label = close_wide.shift(-forward_period) / close_wide - 1

    return data_dict, label
