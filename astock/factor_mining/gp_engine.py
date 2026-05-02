"""
遗传规划引擎（DEAP）
====================
"""
import operator
import random
from functools import partial

import numpy as np
import pandas as pd
from deap import base, creator, tools, gp, algorithms

from . import operators
from .fitness import evaluate_ic


def _rand_const():
    return random.uniform(-1, 1)


class FactorGPEngine:
    """
    GP 因子挖掘引擎

    Parameters
    ----------
    data_dict : dict[str, pd.DataFrame]
    label_df : pd.DataFrame
    pop_size : int
    generations : int
    cxpb : float
        交叉概率
    mutpb : float
        变异概率
    tournsize : int
    max_depth : int
        树最大深度，防止过拟合
    """

    def __init__(
        self,
        data_dict,
        label_df,
        pop_size=100,
        generations=20,
        cxpb=0.5,
        mutpb=0.2,
        tournsize=3,
        max_depth=5,
    ):
        self.data_dict = data_dict
        self.label_df = label_df
        self.pop_size = pop_size
        self.generations = generations
        self.cxpb = cxpb
        self.mutpb = mutpb
        self.tournsize = tournsize
        self.max_depth = max_depth

        self._build_pset()
        self._setup_deap()

    # ------------------------------------------------------------------
    # 构建算子集
    # ------------------------------------------------------------------
    def _build_pset(self):
        self.pset = gp.PrimitiveSet("MAIN", 0)

        # Terminals: 基础数据字段（占位符字符串，context 中映射为真实 DataFrame）
        for name in self.data_dict.keys():
            self.pset.addTerminal(name, name=name)

        # 覆盖 context，使 compile 时字符串名称解析为真实 DataFrame
        for name, df in self.data_dict.items():
            self.pset.context[name] = df

        # 随机常数（用 partial 避免 lambda 无法 pickle 的 warning）
        self.pset.addEphemeralConstant("rand", partial(_rand_const))

        # 一元时间序列算子
        for w in [5, 10, 20]:
            self.pset.addPrimitive(
                partial(operators.ts_mean, window=w), 1, name=f"ts_mean_{w}"
            )
            self.pset.addPrimitive(
                partial(operators.ts_std, window=w), 1, name=f"ts_std_{w}"
            )
            self.pset.addPrimitive(
                partial(operators.ts_max, window=w), 1, name=f"ts_max_{w}"
            )
            self.pset.addPrimitive(
                partial(operators.ts_min, window=w), 1, name=f"ts_min_{w}"
            )
        for w in [1, 5]:
            self.pset.addPrimitive(
                partial(operators.ts_delay, window=w), 1, name=f"ts_delay_{w}"
            )
            self.pset.addPrimitive(
                partial(operators.ts_delta, window=w), 1, name=f"ts_delta_{w}"
            )
        for w in [5, 10]:
            self.pset.addPrimitive(
                partial(operators.ts_rank, window=w), 1, name=f"ts_rank_{w}"
            )

        # 横截面算子
        self.pset.addPrimitive(operators.cs_rank, 1, name="cs_rank")
        self.pset.addPrimitive(operators.cs_zscore, 1, name="cs_zscore")

        # 一元数学算子
        self.pset.addPrimitive(operators.neg, 1, name="neg")
        self.pset.addPrimitive(operators.abs_op, 1, name="abs")
        self.pset.addPrimitive(operators.sign, 1, name="sign")
        self.pset.addPrimitive(operators.log_op, 1, name="log")

        # 二元算子
        self.pset.addPrimitive(operators.add, 2, name="add")
        self.pset.addPrimitive(operators.sub, 2, name="sub")
        self.pset.addPrimitive(operators.mul, 2, name="mul")
        self.pset.addPrimitive(operators.div, 2, name="div")
        self.pset.addPrimitive(operators.max_op, 2, name="max")
        self.pset.addPrimitive(operators.min_op, 2, name="min")

        # 二元时间序列算子
        for w in [5, 10]:
            self.pset.addPrimitive(
                partial(operators.ts_corr, window=w), 2, name=f"ts_corr_{w}"
            )

    # ------------------------------------------------------------------
    # DEAP 配置
    # ------------------------------------------------------------------
    def _setup_deap(self):
        # 若已存在则删除（防止重复 create 报错）
        if hasattr(creator, "FitnessMax"):
            del creator.FitnessMax
        if hasattr(creator, "Individual"):
            del creator.Individual

        creator.create("FitnessMax", base.Fitness, weights=(1.0,))
        creator.create("Individual", gp.PrimitiveTree, fitness=creator.FitnessMax)

        self.toolbox = base.Toolbox()
        self.toolbox.register(
            "expr", gp.genHalfAndHalf, pset=self.pset, min_=1, max_=3
        )
        self.toolbox.register(
            "individual", tools.initIterate, creator.Individual, self.toolbox.expr
        )
        self.toolbox.register(
            "population", tools.initRepeat, list, self.toolbox.individual
        )
        self.toolbox.register("evaluate", self._eval_individual)
        self.toolbox.register(
            "select", tools.selTournament, tournsize=self.tournsize
        )
        self.toolbox.register("mate", gp.cxOnePoint)
        self.toolbox.register(
            "expr_mut", gp.genFull, min_=0, max_=2
        )
        self.toolbox.register(
            "mutate", gp.mutUniform, expr=self.toolbox.expr_mut, pset=self.pset
        )

        self.toolbox.decorate(
            "mate",
            gp.staticLimit(
                key=operator.attrgetter("height"), max_value=self.max_depth
            ),
        )
        self.toolbox.decorate(
            "mutate",
            gp.staticLimit(
                key=operator.attrgetter("height"), max_value=self.max_depth
            ),
        )

    # ------------------------------------------------------------------
    # 个体评估
    # ------------------------------------------------------------------
    def _eval_individual(self, individual):
        try:
            # 注意：pset arity=0 时 gp.compile 直接返回执行结果，不是函数
            factor = gp.compile(expr=individual, pset=self.pset)

            if not isinstance(factor, pd.DataFrame) or factor.empty:
                return (-1.0,)

            mean_ic, ir, _ = evaluate_ic(factor, self.label_df)

            # 适应度：abs(IC) * min(|IR|, 2)，兼顾预测能力和稳定性
            fitness = abs(mean_ic) * min(abs(ir), 2.0)

            # NaN 过多则惩罚
            nan_ratio = factor.isna().sum().sum() / (
                factor.shape[0] * factor.shape[1] + 1e-8
            )
            if nan_ratio > 0.3:
                fitness *= 0.5

            # 因子值全相同（无区分度）则无效
            if factor.nunique().sum() <= 1:
                return (-1.0,)

            return (fitness,)
        except Exception:
            return (-1.0,)

    # ------------------------------------------------------------------
    # 运行进化
    # ------------------------------------------------------------------
    def run(self, verbose=True):
        pop = self.toolbox.population(n=self.pop_size)
        hof = tools.HallOfFame(5)

        stats = tools.Statistics(lambda ind: ind.fitness.values[0])
        stats.register("avg", np.mean)
        stats.register("std", np.std)
        stats.register("min", np.min)
        stats.register("max", np.max)

        pop, log = algorithms.eaSimple(
            pop,
            self.toolbox,
            cxpb=self.cxpb,
            mutpb=self.mutpb,
            ngen=self.generations,
            stats=stats,
            halloffame=hof,
            verbose=verbose,
        )
        return pop, log, hof
