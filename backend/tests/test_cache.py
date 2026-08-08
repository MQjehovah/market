"""内存 TTL 缓存测试。"""

import time

import pytest

from app.cache import MemoryCache


def test_cache_set_get_delete():
    cache = MemoryCache()
    cache.set("k", {"a": 1}, ttl=60)
    assert cache.get("k") == {"a": 1}
    cache.delete("k")
    assert cache.get("k") is None


def test_cache_ttl_expiry():
    cache = MemoryCache()
    cache.set("k", "v", ttl=1)
    assert cache.get("k") == "v"
    time.sleep(1.1)
    assert cache.get("k") is None


def test_cache_clear():
    cache = MemoryCache()
    cache.set("a", 1)
    cache.set("b", 2)
    cache.clear()
    assert cache.get("a") is None
    assert cache.get("b") is None
