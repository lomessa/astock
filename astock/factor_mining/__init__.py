"""
遗传规划因子挖掘模块
====================
基于 DEAP 的自动化因子发现系统，
支持时间序列与横截面算子，适配 AStock 数据体系。
"""

from .gp_engine import FactorGPEngine
from .data_prep import prepare_data_from_db
from .fitness import evaluate_ic

__all__ = ["FactorGPEngine", "prepare_data_from_db", "evaluate_ic"]
