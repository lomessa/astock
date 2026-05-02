"""
Baostock 数据源实现
===================
免费 A 股历史行情源，重点替代 get_daily / get_index_daily。
Baostock 代码格式：sh.600000 / sz.000001
"""
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from astock.data.sources.base import DataSource


def _to_bs_code(symbol: str) -> str:
    """000001 -> sh.000001 / sz.000001"""
    symbol = str(symbol).zfill(6)
    if symbol.startswith(("6", "5", "9", "68", "88")):
        return f"sh.{symbol}"
    return f"sz.{symbol}"


def _from_bs_code(bs_code: str) -> str:
    """sh.600000 -> 600000"""
    return bs_code.split(".")[-1].zfill(6)


class BaostockSource(DataSource):
    """Baostock 数据源（免费）"""

    name = "baostock"
    _login_ok = False

    def _ensure_login(self):
        if self._login_ok:
            return
        try:
            import baostock as bs
            lg = bs.login()
            if lg.error_code == "0":
                self._login_ok = True
            else:
                raise RuntimeError(f"Baostock login failed: {lg.error_msg}")
        except Exception as e:
            raise RuntimeError(f"Baostock login exception: {e}")

    def get_daily(self, symbol: str, start_date: Optional[str] = None,
                  end_date: Optional[str] = None) -> pd.DataFrame:
        self._ensure_login()
        import baostock as bs

        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        else:
            # 20240101 -> 2024-01-01
            end_date = pd.to_datetime(end_date).strftime("%Y-%m-%d")
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        else:
            start_date = pd.to_datetime(start_date).strftime("%Y-%m-%d")

        fields = "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg"
        rs = bs.query_history_k_data_plus(
            _to_bs_code(symbol), fields,
            start_date=start_date, end_date=end_date,
            frequency="d", adjustflag="3"  # 前复权
        )
        data_list = []
        while (rs.error_code == "0") and rs.next():
            data_list.append(rs.get_row_data())
        if not data_list:
            return pd.DataFrame()

        df = pd.DataFrame(data_list, columns=rs.fields)
        df = df.rename(columns={
            "date": "date", "code": "symbol_bs",
            "open": "open", "high": "high", "low": "low",
            "close": "close", "preclose": "pre_close",
            "volume": "volume", "amount": "amount",
            "turn": "turnover", "pctChg": "change_pct",
        })
        df["symbol"] = df["symbol_bs"].apply(_from_bs_code)
        df = df.drop(columns=["symbol_bs"])
        df["date"] = pd.to_datetime(df["date"])
        for col in ["open", "high", "low", "close", "pre_close", "volume", "amount", "turnover", "change_pct"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.sort_values("date").reset_index(drop=True)
        return df

    def get_index_daily(self, symbol: str = "000001") -> pd.DataFrame:
        self._ensure_login()
        import baostock as bs
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=365 * 2)).strftime("%Y-%m-%d")
        fields = "date,code,open,high,low,close,preclose,volume,amount,pctChg"
        rs = bs.query_history_k_data_plus(
            _to_bs_code(symbol), fields,
            start_date=start_date, end_date=end_date,
            frequency="d", adjustflag="3"
        )
        data_list = []
        while (rs.error_code == "0") and rs.next():
            data_list.append(rs.get_row_data())
        if not data_list:
            return pd.DataFrame()
        df = pd.DataFrame(data_list, columns=rs.fields)
        df = df.rename(columns={
            "date": "date", "open": "open", "high": "high",
            "low": "low", "close": "close", "volume": "volume",
            "amount": "amount", "pctChg": "change_pct",
        })
        df["date"] = pd.to_datetime(df["date"])
        for col in ["open", "high", "low", "close", "volume", "amount", "change_pct"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.sort_values("date").reset_index(drop=True)
        return df
