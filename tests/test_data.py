"""
数据模块测试（更新版）
======================
测试 DataClient 的聚合逻辑与 LocalStore 的持久化。
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta

import pandas as pd

from astock.data.store import LocalStore
from astock.data.client import DataClient
from astock.data.sources.base import DataSource


class MockSource(DataSource):
    """模拟数据源，用于测试 fallback 逻辑"""
    name = "mock"

    def __init__(self, fail=False):
        self.fail = fail
        self.call_count = 0

    def get_daily(self, symbol, start_date=None, end_date=None):
        self.call_count += 1
        if self.fail:
            raise RuntimeError("mock fail")
        return pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=3),
            "open": [1, 2, 3],
            "high": [2, 3, 4],
            "low": [0, 1, 2],
            "close": [1.5, 2.5, 3.5],
            "volume": [100, 200, 300],
            "symbol": [symbol.zfill(6)] * 3,
        })

    def get_stock_info(self):
        if self.fail:
            raise RuntimeError("mock fail")
        return pd.DataFrame({
            "symbol": ["000001", "600000"],
            "name": ["平安银行", "浦发银行"],
            "price": [10.0, 8.0],
        })


class TestLocalStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        self.store = LocalStore(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_save_and_load_daily(self):
        df = pd.DataFrame({
            "symbol": ["000001", "000001", "000001"],
            "date": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "open": [1.0, 2.0, 3.0],
            "close": [1.5, 2.5, 3.5],
            "volume": [100, 200, 300],
        })
        self.store.save_daily(df)
        loaded = self.store.load_daily("000001", "2024-01-01", "2024-01-03")
        self.assertEqual(len(loaded), 3)
        self.assertIn("close", loaded.columns)

    def test_daily_coverage(self):
        df = pd.DataFrame({
            "symbol": ["000001"] * 5,
            "date": pd.date_range("2024-01-01", periods=5).strftime("%Y-%m-%d"),
            "open": [1.0] * 5, "close": [1.5] * 5, "volume": [100] * 5,
        })
        self.store.save_daily(df)
        self.assertTrue(self.store.daily_coverage("000001", "2024-01-02", "2024-01-04"))
        self.assertFalse(self.store.daily_coverage("000001", "2024-01-01", "2024-01-10"))

    def test_stock_info_ttl(self):
        df = pd.DataFrame({
            "symbol": ["000001"], "name": ["平安银行"], "price": [10.0],
        })
        self.store.save_stock_info(df)
        loaded = self.store.load_stock_info(max_age_minutes=30)
        self.assertFalse(loaded.empty)


class TestDataClient(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "client.db")
        self.client = DataClient()
        self.client.store = LocalStore(self.db_path)
        self.client._sources = {}

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_fallback_to_source(self):
        """本地无数据时，应 fallback 到外部源，并写回本地"""
        mock = MockSource(fail=False)
        self.client._sources["mock"] = mock
        self.client._METHOD_PRIORITY["get_daily"] = ["mock"]

        df = self.client.get_daily("000001", start_date="2024-01-01", end_date="2024-01-03")
        self.assertFalse(df.empty)
        self.assertEqual(mock.call_count, 1)

        # 再次调用应从本地读取，不再访问外部源
        df2 = self.client.get_daily("000001", start_date="2024-01-01", end_date="2024-01-03")
        self.assertFalse(df2.empty)
        self.assertEqual(mock.call_count, 1)  # 未增加

    def test_fallback_when_source_fails(self):
        """主源失败时应尝试备用源"""
        fail_mock = MockSource(fail=True)
        ok_mock = MockSource(fail=False)
        self.client._sources["fail"] = fail_mock
        self.client._sources["ok"] = ok_mock
        self.client._METHOD_PRIORITY["get_daily"] = ["fail", "ok"]

        df = self.client.get_daily("000001")
        self.assertFalse(df.empty)
        self.assertEqual(fail_mock.call_count, 1)
        self.assertEqual(ok_mock.call_count, 1)

    def test_all_sources_fail_returns_empty(self):
        """全部源失败时返回空 DataFrame"""
        fail_mock = MockSource(fail=True)
        self.client._sources["fail"] = fail_mock
        self.client._METHOD_PRIORITY["get_daily"] = ["fail"]

        df = self.client.get_daily("000001")
        self.assertTrue(df.empty)


if __name__ == "__main__":
    unittest.main()
