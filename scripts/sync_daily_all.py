#!/usr/bin/env python3
"""
批量同步全市场 A 股日线数据到本地 SQLite
===========================================
用法:
    python scripts/sync_daily_all.py          # 同步全部（自动跳过已有数据的）
    python scripts/sync_daily_all.py --force  # 强制覆盖已有数据
    python scripts/sync_daily_all.py --max 500  # 只同步前500只
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# 把项目根目录加入路径
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from astock.data.store import LocalStore


def get_a_shares(db_path: str) -> list:
    """从 stock_info 读取 A 股代码列表"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, name FROM stock_info
        WHERE symbol REGEXP '^[0-9]{6}$'
          AND (symbol LIKE '60%' OR symbol LIKE '00%' OR symbol LIKE '30%' OR symbol LIKE '68%')
        ORDER BY symbol
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def _to_bs_code(symbol: str) -> str:
    """转为 baostock 格式"""
    if symbol.startswith(("6", "88", "11")):
        return f"sh.{symbol}"
    return f"sz.{symbol}"


def already_has_data(conn: sqlite3.Connection, symbol: str, start: str, end: str) -> bool:
    """检查某只股票在指定区间是否已有数据"""
    cur = conn.execute(
        "SELECT COUNT(*) FROM daily WHERE symbol=? AND date>=? AND date<=?",
        (symbol, start, end)
    )
    return cur.fetchone()[0] > 5


def sync_daily_all(db_path: str, lookback_days: int = 90, max_stocks: int = None, force: bool = False):
    try:
        import baostock as bs
    except ImportError:
        print("[ERROR] 请先安装 baostock: pip install baostock")
        return

    store = LocalStore(db_path)

    # 获取 A 股列表
    try:
        rows = get_a_shares(db_path)
    except sqlite3.OperationalError as e:
        print(f"[ERROR] 读取 stock_info 失败: {e}")
        print("  提示: stock_info 表可能不存在或使用了 REGEXP 但不支持。改用简单查询...")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT symbol, name FROM stock_info WHERE LENGTH(symbol)=6 ORDER BY symbol")
        rows = cur.fetchall()
        conn.close()

    if not rows:
        print("[WARN] stock_info 表为空，请先同步股票列表")
        return

    print(f"[Info] stock_info 共 {len(rows)} 只股票")

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=lookback_days + 30)).strftime("%Y-%m-%d")
    print(f"[Info] 数据区间: {start_date} ~ {end_date}")

    if max_stocks:
        rows = rows[:max_stocks]
        print(f"[Info] 本次仅同步前 {max_stocks} 只")

    # 登录 baostock
    lg = bs.login()
    if lg.error_code != "0":
        print(f"[ERROR] Baostock 登录失败: {lg.error_msg}")
        return

    conn = sqlite3.connect(db_path)
    success = 0
    skip = 0
    fail = 0
    total = len(rows)

    for i, (symbol, name) in enumerate(rows, 1):
        if i % 100 == 0 or i == total:
            print(f"  进度 {i}/{total} | 成功:{success} 跳过:{skip} 失败:{fail}")

        # 检查是否已有数据
        if not force and already_has_data(conn, symbol, start_date, end_date):
            skip += 1
            continue

        code = _to_bs_code(symbol)
        try:
            rs = bs.query_history_k_data_plus(
                code,
                "date,open,high,low,close,volume,amount,turn,pctChg",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="3",
            )
            data = []
            while rs.error_code == "0" and rs.next():
                data.append(rs.get_row_data())

            if len(data) < 10:
                fail += 1
                continue

            df = pd.DataFrame(data, columns=[
                "date", "open", "high", "low", "close",
                "volume", "amount", "turnover", "change_pct"
            ])
            for col in ["open", "high", "low", "close", "volume", "amount", "turnover", "change_pct"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["symbol"] = symbol
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            df = df.dropna(subset=["close"])

            if df.empty:
                fail += 1
                continue

            store.save_daily(df)
            success += 1
        except Exception as e:
            fail += 1
            if i <= 5 or i % 200 == 0:
                print(f"    [WARN] {symbol} {name} 拉取失败: {e}")

    bs.logout()
    conn.close()
    print(f"\n✅ 同步完成: 成功 {success} | 跳过 {skip} | 失败 {fail}")
    print(f"   当前 daily 表总股票数: {store._conn().execute('SELECT COUNT(DISTINCT symbol) FROM daily').fetchone()[0]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="批量同步 A 股日线数据")
    parser.add_argument("--db", default="/root/.astock/astock.db", help="SQLite 路径")
    parser.add_argument("--lookback", type=int, default=90, help="回看天数")
    parser.add_argument("--max", type=int, dest="max_stocks", default=None, help="最多同步几只")
    parser.add_argument("--force", action="store_true", help="强制覆盖已有数据")
    args = parser.parse_args()

    sync_daily_all(args.db, args.lookback, args.max_stocks, args.force)
