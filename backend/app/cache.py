"""缓存抽象：内存 TTL 缓存（默认）或 Redis。"""

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


class RedisCache:
    def __init__(self) -> None:
        import redis.asyncio as aioredis

        self._client = aioredis.from_url(get_settings().redis_url, decode_responses=True)

    async def get(self, key: str) -> Any | None:
        try:
            return await self._client.get(key)
        except Exception:
            return None

    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        try:
            await self._client.set(key, value, ex=ttl)
        except Exception:
            pass

    async def delete(self, key: str) -> None:
        try:
            await self._client.delete(key)
        except Exception:
            pass


_memory_cache = MemoryCache()
_redis_cache: RedisCache | None = None


def get_cache() -> MemoryCache | RedisCache:
    settings = get_settings()
    global _redis_cache
    if settings.cache_backend == "redis":
        if _redis_cache is None:
            _redis_cache = RedisCache()
        return _redis_cache
    return _memory_cache
