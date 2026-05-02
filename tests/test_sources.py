"""
数据源接口测试
==============
验证各数据源可正确导入并拥有统一接口。
"""
import unittest


class TestSourceImports(unittest.TestCase):
    def test_base_has_required_methods(self):
        from astock.data.sources.base import DataSource
        required = [
            "get_daily", "get_last_price",
            "get_zt_pool", "get_zt_pool_previous",
            "get_zt_pool_strong", "get_zt_pool_sub_new", "get_zt_pool_zbgc",
            "get_stock_info", "get_realtime_quotes",
            "get_sector_hot", "get_longhubang",
            "get_limit_up_statistics", "get_index_daily",
            "clear_cache",
        ]
        for m in required:
            self.assertTrue(hasattr(DataSource, m), f"DataSource 缺少方法: {m}")

    def test_akshare_source_import(self):
        from astock.data.sources.akshare_src import AKShareSource
        self.assertEqual(AKShareSource.name, "akshare")

    def test_baostock_source_import(self):
        from astock.data.sources.baostock_src import BaostockSource
        self.assertEqual(BaostockSource.name, "baostock")

    def test_tushare_source_import(self):
        from astock.data.sources.tushare_src import TushareSource
        self.assertEqual(TushareSource.name, "tushare")

    def test_yahoo_source_import(self):
        from astock.data.sources.yahoo_src import YahooSource
        self.assertEqual(YahooSource.name, "yahoo")


if __name__ == "__main__":
    unittest.main()
