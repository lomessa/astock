"""
算子库
======
所有算子均接受 pd.DataFrame（宽格式，index=date, columns=symbol）
或标量，返回 pd.DataFrame 或标量。
pandas 会自动处理 broadcast。
"""
import numpy as np
import pandas as pd


# ------------------------------------------------------------------
# 时间序列算子（对每只股票的历史序列操作）
# ------------------------------------------------------------------
def ts_mean(a, window):
    return a.rolling(window=window, min_periods=1).mean()


def ts_std(a, window):
    return a.rolling(window=window, min_periods=1).std()


def ts_max(a, window):
    return a.rolling(window=window, min_periods=1).max()


def ts_min(a, window):
    return a.rolling(window=window, min_periods=1).min()


def ts_delay(a, window):
    return a.shift(window)


def ts_delta(a, window):
    return a - a.shift(window)


def ts_rank(a, window):
    """滚动排名，输出 0~1（最后一个值在窗口内的分位）"""
    def _rank_last(x):
        # x: 1-D numpy array
        if len(x) <= 1:
            return 0.5
        ranks = np.argsort(np.argsort(x))
        return ranks[-1] / (len(x) - 1)

    return a.rolling(window=window, min_periods=1).apply(
        _rank_last, raw=True
    )


def ts_corr(a, b, window):
    """a 与 b 的滚动相关系数"""
    return a.rolling(window=window, min_periods=3).corr(b)


# ------------------------------------------------------------------
# 横截面算子（对每天的所有股票操作）
# ------------------------------------------------------------------
def cs_rank(a):
    """每日截面相关系数排名，缩放到 0~1"""
    return a.rank(axis=1, pct=True)


def cs_zscore(a):
    """每日截面标准化"""
    return (a - a.mean(axis=1)) / (a.std(axis=1) + 1e-8)


# ------------------------------------------------------------------
# 数学算子
# ------------------------------------------------------------------
def add(a, b):
    return a + b


def sub(a, b):
    return a - b


def mul(a, b):
    return a * b


def div(a, b):
    """保护除法"""
    return a / (b + 1e-8)


def max_op(a, b):
    return np.maximum(a, b)


def min_op(a, b):
    return np.minimum(a, b)


def neg(a):
    return -a


def abs_op(a):
    return np.abs(a)


def sign(a):
    return np.sign(a)


def log_op(a):
    return np.log(np.abs(a) + 1e-8)
