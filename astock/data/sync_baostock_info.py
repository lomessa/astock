"""
基于 Baostock 同步全市场股票快照（支持分批断点续传）
=========================================================
"""
import baostock as bs
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional

from astock.data.store import LocalStore


def _to_symbol(bs_code: str) -> str:
    return bs_code.split(".")[-1].zfill(6)


def sync_stock_info_from_baostock(
    db_path: Optional[str] = None,
    batch_size: Optional[int] = None,
    offset: int = 0,
):
    store = LocalStore(db_path)

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"Baostock login failed: {lg.error_msg}")

    # 获取 A 股列表
    rs = bs.query_stock_basic()
    stocks = []
    while rs.error_code == "0" and rs.next():
        row = rs.get_row_data()
        code, name, ipo_date, out_date, typ, status = row
        if typ != "1":
            continue
        if status != "1":
            continue
        if out_date and out_date != "":
            continue
        sym = code.split(".")[-1]
        if sym.startswith(("2", "4", "8", "9")):
            continue
        stocks.append({"code": code, "name": name})

    bs.logout()
    total = len(stocks)
    print(f"[Baostock] 总计 {total} 只 A 股，本次同步 offset={offset} batch={batch_size}")

    if offset >= total:
        print("⚠️ offset 超出范围")
        return pd.DataFrame()

    end_idx = offset + batch_size if batch_size else total
    stocks = stocks[offset:end_idx]

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=70)).strftime("%Y-%m-%d")

    results = []
    batch_total = len(stocks)
    lg = bs.login()

    for i, item in enumerate(stocks, 1):
        code = item["code"]
        name = item["name"]

        if i % 100 == 0 or i == batch_total:
            print(f"  进度 {i}/{batch_total}，成功 {len(results)}")

        try:
            rs = bs.query_history_k_data_plus(
                code,
                "date,close,turn,pctChg",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="3",
            )
            data = []
            while rs.error_code == "0" and rs.next():
                data.append(rs.get_row_data())

            if len(data) < 20:
                continue

            df = pd.DataFrame(data, columns=["date", "close", "turnover", "change_pct"])
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df["turnover"] = pd.to_numeric(df["turnover"], errors="coerce")
            df["change_pct"] = pd.to_numeric(df["change_pct"], errors="coerce")

            latest = df.iloc[-1]
            idx_60 = -60 if len(df) >= 60 else 0
            close_60d_ago = df["close"].iloc[idx_60]
            change_60d = (latest["close"] - close_60d_ago) / close_60d_ago * 100.0

            start_of_year = datetime.now().strftime("%Y-01-01")
            ytd_df = df[df["date"] >= start_of_year]
            if not ytd_df.empty:
                change_ytd = (latest["close"] - ytd_df["close"].iloc[0]) / ytd_df["close"].iloc[0] * 100.0
            else:
                change_ytd = change_60d

            results.append({
                "symbol": _to_symbol(code),
                "name": name,
                "price": round(latest["close"], 2),
                "change_pct": round(float(latest["change_pct"]) if pd.notna(latest["change_pct"]) else 0, 2),
                "turnover": round(float(latest["turnover"]) if pd.notna(latest["turnover"]) else 0, 2),
                "change_60d": round(change_60d, 2),
                "change_ytd": round(change_ytd, 2),
            })
        except Exception:
            continue

    bs.logout()

    if not results:
        print("⚠️ 没有获取到任何股票数据")
        return pd.DataFrame()

    df_result = pd.DataFrame(results)
    store.save_stock_info(df_result)
    print(f"✅ 已写入本地 SQLite: {len(df_result)} 只股票")
    return df_result


if __name__ == "__main__":
    import sys
    offset = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    batch = int(sys.argv[2]) if len(sys.argv) > 2 else None
    sync_stock_info_from_baostock(offset=offset, batch_size=batch)
