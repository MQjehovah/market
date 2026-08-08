"""内存 TTL 缓存（单进程部署默认方案，不依赖外部服务）。"""

import time
from typing import Any

from app.config import get_settings


class MemoryCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        item = self._store.get(key)
        if item is None:
            return None
        expire_at, value = item
        if expire_at < time.time():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        self._store[key] = (time.time() + ttl, value)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()


_memory_cache = MemoryCache()


def get_cache() -> MemoryCache:
    settings = get_settings()
    if not settings.cache_enabled:
        return _NullCache()
    return _memory_cache


class _NullCache(MemoryCache):
    """缓存关闭时的空实现：get 恒为 None，set/delete/clear 为空操作。"""

    def get(self, key: str) -> Any | None:
        return None

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        return None

    def clear(self) -> None:
        return None


def invalidate_marketplace_cache() -> None:
    """数据变更后整体失效市场缓存（浏览/分类/统计）。"""
    get_cache().clear()
