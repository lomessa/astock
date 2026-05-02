"""
AKShareData 兼容层（已废弃）
===========================
请迁移到 DataClient，支持多源聚合与本地持久化。
"""
import warnings

from astock.data.sources.akshare_src import AKShareSource


class AKShareData(AKShareSource):
    """保留旧类名以兼容现有代码，内部实际使用 AKShareSource。"""

    def __init__(self):
        warnings.warn(
            "AKShareData is deprecated and will be removed in a future version. "
            "Use astock.data.DataClient for multi-source aggregation and local persistence.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__()
