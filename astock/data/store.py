"""
SQLite 本地持久化层
===================
把外部拉取的数据写入 SQLite，日常优先从本地读取。
"""
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

import pandas as pd

from astock.config import get_settings


class LocalStore:
    """本地 SQLite 数据存储"""

    def __init__(self, db_path: Optional[str] = None):
        cfg = get_settings().data
        self.db_path = db_path or cfg.db_path
        self._ensure_dir()
        self._init_tables()

    def _ensure_dir(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_tables(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL,
                    volume REAL, amount REAL,
                    change_pct REAL, turnover REAL,
                    PRIMARY KEY (symbol, date)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS zt_pool (
                    trade_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    change_pct REAL, close REAL, amount REAL,
                    float_mv REAL, total_mv REAL, turnover REAL,
                    seal_amount REAL, first_seal_time TEXT,
                    last_seal_time TEXT, open_times INTEGER,
                    zt_stats TEXT, board_num INTEGER, industry TEXT,
                    PRIMARY KEY (trade_date, symbol)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS zt_pool_prev (
                    trade_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    change_pct REAL, close REAL, amount REAL,
                    float_mv REAL, turnover REAL,
                    zt_stats TEXT, industry TEXT,
                    PRIMARY KEY (trade_date, symbol)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stock_info (
                    symbol TEXT PRIMARY KEY,
                    name TEXT,
                    price REAL, change_pct REAL,
                    volume REAL, amount REAL,
                    turnover REAL, total_mv REAL, float_mv REAL,
                    pe_ttm REAL, pb REAL,
                    change_60d REAL, change_ytd REAL,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS index_daily (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL,
                    volume REAL, change_pct REAL,
                    PRIMARY KEY (symbol, date)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sector_rank (
                    rank_date TEXT NOT NULL,
                    sector_name TEXT NOT NULL,
                    sector_rank INTEGER,
                    main_inflow REAL,
                    change_pct REAL,
                    PRIMARY KEY (rank_date, sector_name)
                )
            """)
            conn.commit()

    def _save_df(self, conn: sqlite3.Connection, table: str, df: pd.DataFrame,
                 pk_cols: List[str]):
        if df.empty:
            return
        df = df.copy()
        for col in df.columns:
            if df[col].dtype.name == "datetime64[ns]":
                df[col] = df[col].dt.strftime("%Y-%m-%d")
        # 只保留表中存在的列
        existing_cols = [d[1] for d in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        cols = [c for c in df.columns if c in existing_cols]
        if not cols:
            return
        df = df[cols].drop_duplicates(subset=pk_cols)
        placeholders = ", ".join(["?"] * len(cols))
        col_str = ", ".join(cols)
        sql = f"INSERT OR REPLACE INTO {table} ({col_str}) VALUES ({placeholders})"
        rows = [tuple(row) for row in df.values]
        conn.executemany(sql, rows)
        conn.commit()

    def _load_df(self, conn: sqlite3.Connection, table: str,
                 where: str = "", params: tuple = ()) -> pd.DataFrame:
        sql = f"SELECT * FROM {table}"
        if where:
            sql += f" WHERE {where}"
        df = pd.read_sql_query(sql, conn, params=params)
        for col in ["date", "trade_date", "updated_at", "rank_date"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        return df

    # ------------------------------------------------------------------
    # Daily
    # ------------------------------------------------------------------
    def save_daily(self, df: pd.DataFrame):
        if df.empty or "symbol" not in df.columns:
            return
        with self._conn() as conn:
            self._save_df(conn, "daily", df, pk_cols=["symbol", "date"])

    def load_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        with self._conn() as conn:
            df = self._load_df(
                conn, "daily",
                "symbol = ? AND date >= ? AND date <= ?",
                (symbol.zfill(6), start_date, end_date)
            )
        if not df.empty:
            for col in ["open", "high", "low", "close", "volume", "amount", "change_pct", "turnover"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.sort_values("date").reset_index(drop=True)
        return df

    def daily_coverage(self, symbol: str, start_date: str, end_date: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT MIN(date), MAX(date) FROM daily WHERE symbol = ?",
                (symbol.zfill(6),)
            )
            row = cur.fetchone()
        if row is None or row[0] is None:
            return False
        min_date, max_date = row[0], row[1]
        # 本地数据边界各放宽1天，覆盖请求区间即可
        local_start = (pd.to_datetime(min_date) - timedelta(days=1)).strftime("%Y-%m-%d")
        local_end = (pd.to_datetime(max_date) + timedelta(days=1)).strftime("%Y-%m-%d")
        return local_start <= start_date and local_end >= end_date

    # ------------------------------------------------------------------
    # ZT Pool
    # ------------------------------------------------------------------
    def save_zt_pool(self, df: pd.DataFrame):
        if df.empty:
            return
        with self._conn() as conn:
            self._save_df(conn, "zt_pool", df, pk_cols=["trade_date", "symbol"])

    def load_zt_pool(self, trade_date: str) -> pd.DataFrame:
        date_str = pd.to_datetime(trade_date).strftime("%Y-%m-%d")
        with self._conn() as conn:
            df = self._load_df(conn, "zt_pool", "trade_date = ?", (date_str,))
        if not df.empty and "symbol" in df.columns:
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        return df

    # ------------------------------------------------------------------
    # ZT Pool Previous
    # ------------------------------------------------------------------
    def save_zt_pool_prev(self, df: pd.DataFrame):
        if df.empty:
            return
        with self._conn() as conn:
            self._save_df(conn, "zt_pool_prev", df, pk_cols=["trade_date", "symbol"])

    def load_zt_pool_prev(self, trade_date: str) -> pd.DataFrame:
        date_str = pd.to_datetime(trade_date).strftime("%Y-%m-%d")
        with self._conn() as conn:
            df = self._load_df(conn, "zt_pool_prev", "trade_date = ?", (date_str,))
        if not df.empty and "symbol" in df.columns:
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        return df

    # ------------------------------------------------------------------
    # Stock Info
    # ------------------------------------------------------------------
    def save_stock_info(self, df: pd.DataFrame):
        if df.empty:
            return
        df = df.copy()
        df["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as conn:
            self._save_df(conn, "stock_info", df, pk_cols=["symbol"])

    def load_stock_info(self, max_age_minutes: int = 30) -> pd.DataFrame:
        with self._conn() as conn:
            df = self._load_df(conn, "stock_info")
        if df.empty:
            return df
        if "updated_at" in df.columns:
            last_update = df["updated_at"].max()
            if pd.isna(last_update):
                return pd.DataFrame()
            if (datetime.now() - last_update).total_seconds() > max_age_minutes * 60:
                return pd.DataFrame()
        if "symbol" in df.columns:
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        return df

    # ------------------------------------------------------------------
    # Index Daily
    # ------------------------------------------------------------------
    def save_index_daily(self, df: pd.DataFrame):
        if df.empty:
            return
        with self._conn() as conn:
            self._save_df(conn, "index_daily", df, pk_cols=["symbol", "date"])

    def load_index_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        with self._conn() as conn:
            df = self._load_df(
                conn, "index_daily",
                "symbol = ? AND date >= ? AND date <= ?",
                (symbol.zfill(6), start_date, end_date)
            )
        if not df.empty:
            for col in ["open", "high", "low", "close", "volume", "change_pct"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.sort_values("date").reset_index(drop=True)
        return df

    def index_daily_coverage(self, symbol: str, start_date: str, end_date: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT MIN(date), MAX(date) FROM index_daily WHERE symbol = ?",
                (symbol.zfill(6),)
            )
            row = cur.fetchone()
        if row is None or row[0] is None:
            return False
        min_date, max_date = row[0], row[1]
        local_start = (pd.to_datetime(min_date) - timedelta(days=1)).strftime("%Y-%m-%d")
        local_end = (pd.to_datetime(max_date) + timedelta(days=1)).strftime("%Y-%m-%d")
        return local_start <= start_date and local_end >= end_date

    # ------------------------------------------------------------------
    # Sector Rank
    # ------------------------------------------------------------------
    def save_sector_rank(self, df: pd.DataFrame, rank_date: str):
        """保存某日的板块排名"""
        if df.empty or "sector_name" not in df.columns:
            return
        df = df.copy()
        df["rank_date"] = pd.to_datetime(rank_date).strftime("%Y-%m-%d")
        with self._conn() as conn:
            self._save_df(conn, "sector_rank", df, pk_cols=["rank_date", "sector_name"])

    def load_sector_rank(self, rank_date: str) -> pd.DataFrame:
        """读取某日的板块排名"""
        date_str = pd.to_datetime(rank_date).strftime("%Y-%m-%d")
        with self._conn() as conn:
            df = self._load_df(conn, "sector_rank", "rank_date = ?", (date_str,))
        if not df.empty:
            for col in ["main_inflow", "change_pct", "sector_rank"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.sort_values("sector_rank").reset_index(drop=True)
        return df

    def load_sector_rank_history(self, days: int = 5) -> pd.DataFrame:
        """读取最近N天的板块排名历史"""
        start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._conn() as conn:
            df = self._load_df(conn, "sector_rank", "rank_date >= ?", (start,))
        if not df.empty:
            for col in ["main_inflow", "change_pct", "sector_rank"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
