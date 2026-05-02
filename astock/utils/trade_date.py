"""交易日工具"""
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import akshare as ak


def get_last_trade_date(date: Optional[str] = None) -> str:
    """获取上一个交易日（简化版：如周五则返回周四；非周五返回昨天）"""
    if date is None:
        dt = datetime.now()
    else:
        dt = datetime.strptime(date, "%Y%m%d")

    # 简单规则：周一→上周五，其他→昨天
    if dt.weekday() == 0:
        dt = dt - timedelta(days=3)
    else:
        dt = dt - timedelta(days=1)
    return dt.strftime("%Y%m%d")


def is_trade_time() -> bool:
    """粗略判断是否处于 A 股交易时段（9:30-11:30, 13:00-15:00）"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return (datetime.strptime("09:30", "%H:%M").time() <= t <= datetime.strptime("11:30", "%H:%M").time() or
            datetime.strptime("13:00", "%H:%M").time() <= t <= datetime.strptime("15:00", "%H:%M").time())


def next_trade_dates(n: int = 5) -> list:
    """获取未来 n 个交易日（简化）"""
    dates = []
    dt = datetime.now()
    while len(dates) < n:
        dt += timedelta(days=1)
        if dt.weekday() < 5:
            dates.append(dt.strftime("%Y%m%d"))
    return dates
