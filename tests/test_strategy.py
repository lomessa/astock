"""策略模块基础测试"""
import unittest
from astock.data.akshare_data import AKShareData
from astock.strategy.zt_strategies import FirstBoardStrategy, ContinuousBoardStrategy
from astock.signal.engine import SignalEngine


class TestStrategy(unittest.TestCase):
    def setUp(self):
        self.data = AKShareData()
        self.engine = SignalEngine(
            data=self.data,
            strategies=[FirstBoardStrategy(), ContinuousBoardStrategy()]
        )

    def test_generate_plans(self):
        """测试信号生成"""
        plans = self.engine.generate_plans()
        print(f"生成计划数: {len(plans)}")
        for p in plans[:3]:
            print(p.to_dict())
        self.assertIsInstance(plans, list)


if __name__ == "__main__":
    unittest.main()
