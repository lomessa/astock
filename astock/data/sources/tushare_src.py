"""
Tushare 数据源实现
==================
需要 token（Pro 版），数据质量高。
Tushare 代码格式：000001.SZ / 600000.SH
"""
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from astock.data.sources.base import DataSource
from astock.config import get_settings


def _to_ts_code(symbol: str) -> str:
    symbol = str(symbol).zfill(6)
    if symbol.startswith(("6", "5", "9", "68", "88")):
        return f"{symbol}.SH"
    return f"{symbol}.SZ"


def _from_ts_code(ts_code: str) -> str:
    return ts_code.split(".")[0].zfill(6)


class TushareSource(DataSource):
    """Tushare Pro 数据源"""

    name = "tushare"

    def __init__(self):
        self._pro = None
        self._token = get_settings().data.tushare_token

    def _ensure_pro(self):
        if self._pro is not None:
            return
        if not self._token:
            raise RuntimeError("Tushare token not configured")
        import tushare as ts
        self._pro = ts.pro_api(self._token)

    def get_daily(self, symbol: str, start_date: Optional[str] = None,
                  end_date: Optional[str] = None) -> pd.DataFrame:
        self._ensure_pro()
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        ts_code = _to_ts_code(symbol)
        df = self._pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns={
            "trade_date": "date", "ts_code": "symbol_ts",
            "open": "open", "high": "high", "low": "low",
            "close": "close", "vol": "volume", "amount": "amount",
            "pct_chg": "change_pct", "pre_close": "pre_close",
        })
        df["symbol"] = _from_ts_code(df["symbol_ts"])
        df = df.drop(columns=["symbol_ts"])
        df["date"] = pd.to_datetime(df["date"])
        for col in ["open", "high", "low", "close", "volume", "amount", "change_pct", "pre_close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.sort_values("date").reset_index(drop=True)
        return df

    def get_stock_info(self) -> pd.DataFrame:
        self._ensure_pro()
        df = self._pro.stock_basic(exchange="", list_status="L")
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns={
            "ts_code": "symbol_ts", "name": "name",
            "industry": "industry", "market": "market",
        })
        df["symbol"] = df["symbol_ts"].apply(lambda x: x.split(".")[0].zfill(6))
        df = df.drop(columns=["symbol_ts"])
        return df

    def get_index_daily(self, symbol: str = "000001") -> pd.DataFrame:
        self._ensure_pro()
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=365 * 2)).strftime("%Y%m%d")
        ts_code = _to_ts_code(symbol)
        df = self._pro.index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns={
            "trade_date": "date", "open": "open", "high": "high",
            "low": "low", "close": "close", "vol": "volume",
            "amount": "amount", "pct_chg": "change_pct",
        })
        df["date"] = pd.to_datetime(df["date"])
        for col in ["open", "high", "low", "close", "volume", "amount", "change_pct"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.sort_values("date").reset_index(drop=True)
        return df
