"""基于 requests_cache 和本地 pickle 的混合缓存。"""
import os
import pickle
import hashlib
from pathlib import Path
from typing import Callable, Any, Optional
from datetime import datetime, timedelta

import pandas as pd


class DataCache:
    """数据缓存管理器"""

    def __init__(self, cache_dir: str, ttl: int = 300):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl  # 秒

    def _key(self, name: str, **kwargs) -> str:
        """生成缓存 key"""
        content = f"{name}:{sorted(kwargs.items())}"
        return hashlib.md5(content.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.pkl"

    def get(self, name: str, **kwargs) -> Optional[Any]:
        """读取缓存"""
        path = self._path(self._key(name, **kwargs))
        if not path.exists():
            return None
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        if (datetime.now() - mtime).total_seconds() > self.ttl:
            return None
        with open(path, "rb") as f:
            return pickle.load(f)

    def set(self, name: str, value: Any, **kwargs):
        """写入缓存"""
        path = self._path(self._key(name, **kwargs))
        with open(path, "wb") as f:
            pickle.dump(value, f)

    def clear(self):
        """清空缓存"""
        for f in self.cache_dir.glob("*.pkl"):
            f.unlink()


def cache_df(ttl: int = 300, cache_dir: Optional[str] = None):
    """装饰器：缓存 DataFrame 返回结果"""
    def decorator(func: Callable) -> Callable:
        _cache = DataCache(cache_dir or (Path.home() / ".astock" / "cache"), ttl=ttl)

        def wrapper(*args, **kwargs):
            # 忽略 self/cls 作为 key
            key_kwargs = kwargs.copy()
            key = f"{func.__name__}:{args[1:]}:{sorted(key_kwargs.items())}"
            cached = _cache.get(key)
            if cached is not None:
                return cached
            result = func(*args, **kwargs)
            if isinstance(result, pd.DataFrame):
                _cache.set(key, result)
            return result
        return wrapper
    return decorator
