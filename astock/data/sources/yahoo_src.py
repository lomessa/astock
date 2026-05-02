"""
Yahoo Finance 数据源实现
=========================
适合获取美股、港股、A 股历史行情。
A 股代码后缀：.SS (上海) / .SZ (深圳)
"""
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from astock.data.sources.base import DataSource


def _to_yf_symbol(symbol: str) -> str:
    """000001 -> 000001.SZ / 600000.SS"""
    symbol = str(symbol).zfill(6)
    if symbol.startswith(("6", "5", "9", "68", "88")):
        return f"{symbol}.SS"
    return f"{symbol}.SZ"


class YahooSource(DataSource):
    """Yahoo Finance 数据源"""

    name = "yahoo"

    def get_daily(self, symbol: str, start_date: Optional[str] = None,
                  end_date: Optional[str] = None) -> pd.DataFrame:
        import yfinance as yf
        yf_symbol = _to_yf_symbol(symbol)
        ticker = yf.Ticker(yf_symbol)

        # yfinance 的 history 接受 datetime 对象
        if end_date is not None:
            end_dt = pd.to_datetime(end_date) + timedelta(days=1)
        else:
            end_dt = datetime.now() + timedelta(days=1)
        if start_date is not None:
            start_dt = pd.to_datetime(start_date)
        else:
            start_dt = end_dt - timedelta(days=365)

        df = ticker.history(start=start_dt, end=end_dt)
        if df.empty:
            return pd.DataFrame()

        df = df.reset_index()
        df = df.rename(columns={
            "Date": "date", "Open": "open", "High": "high",
            "Low": "low", "Close": "close", "Volume": "volume",
        })
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        # 计算 change_pct
        if "close" in df.columns and len(df) > 1:
            df["change_pct"] = df["close"].pct_change() * 100
        df["symbol"] = symbol.zfill(6)
        cols = ["date", "open", "high", "low", "close", "volume", "change_pct", "symbol"]
        df = df[[c for c in cols if c in df.columns]]
        for col in ["open", "high", "low", "close", "volume", "change_pct"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.sort_values("date").reset_index(drop=True)
        return df
