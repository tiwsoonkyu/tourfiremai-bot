"""
v2.lib.cache — Redis wrapper with InMemory fallback.

Single entry point: make_redis(config) returns a RedisLike object that
implements the protocol used by lib/idempotency + lib/memory.

If `config.redis_url` is missing, returns an InMemory implementation
(safe for dev/test; data lost on process restart).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger("v2.cache")


class _InMemoryRedis:
    """Same contract as production RedisClient, no network dependency."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._expiry: dict[str, float] = {}

    def _evict(self) -> None:
        now = time.time()
        expired = [k for k, exp in self._expiry.items() if exp <= now]
        for k in expired:
            self._store.pop(k, None)
            self._expiry.pop(k, None)

    def set(self, key: str, value: str, *, nx: bool = False, ex: Optional[int] = None) -> bool:
        self._evict()
        if nx and key in self._store:
            return False
        self._store[key] = str(value)
        if ex is not None:
            self._expiry[key] = time.time() + ex
        else:
            self._expiry.pop(key, None)
        return True

    def setex(self, key: str, ttl: int, value: str) -> bool:
        return self.set(key, value, ex=ttl)

    def get(self, key: str) -> Optional[str]:
        self._evict()
        return self._store.get(key)

    def delete(self, key: str) -> int:
        self._evict()
        present = 1 if key in self._store else 0
        self._store.pop(key, None)
        self._expiry.pop(key, None)
        return present

    def eval(self, script: str, numkeys: int, *args) -> Any:
        # Minimal CAD emulation
        if numkeys == 1 and len(args) == 2:
            key, expected = args[0], args[1]
            self._evict()
            if self._store.get(key) == expected:
                self._store.pop(key, None)
                self._expiry.pop(key, None)
                return 1
            return 0
        raise NotImplementedError("only compare-and-delete Lua emulated")

    def flushall(self) -> None:
        self._store.clear()
        self._expiry.clear()


class _RealRedis:
    """Thin wrapper over redis-py to expose the same protocol."""

    def __init__(self, client):
        self._c = client

    def set(self, key: str, value: str, *, nx: bool = False, ex: Optional[int] = None) -> bool:
        return bool(self._c.set(key, value, nx=nx, ex=ex))

    def setex(self, key: str, ttl: int, value: str) -> bool:
        return bool(self._c.setex(key, ttl, value))

    def get(self, key: str) -> Optional[str]:
        v = self._c.get(key)
        if v is None:
            return None
        if isinstance(v, bytes):
            return v.decode("utf-8", errors="replace")
        return v

    def delete(self, key: str) -> int:
        return int(self._c.delete(key))

    def eval(self, script: str, numkeys: int, *args) -> Any:
        return self._c.eval(script, numkeys, *args)

    def flushall(self) -> None:
        self._c.flushall()


def make_redis(config) -> Any:
    """
    Construct a RedisLike from Config. Falls back to InMemory if redis_url missing.
    """
    if not config.has_redis:
        logger.warning("V2_STAGING_REDIS_URL not set — using InMemory fallback (NOT for production)")
        return _InMemoryRedis()
    try:
        import redis  # type: ignore
        client = redis.from_url(config.redis_url, decode_responses=True, socket_connect_timeout=5)
        # ping once at construction to validate
        client.ping()
        logger.info("Connected to Redis: %s", _redact_url(config.redis_url))
        return _RealRedis(client)
    except Exception as e:
        logger.error("Redis connection failed (%s) — falling back to InMemory", e)
        return _InMemoryRedis()


def _redact_url(url: str) -> str:
    # rediss://default:PASSWORD@host:port → rediss://default:***@host:port
    import re
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)
