"""
v2.lib.idempotency — Idempotency primitives for V2 webhook ingestion

Implements `V2_IDEMPOTENCY_SPEC.md`:
    - build_meta_message_id(event) → platform-namespaced ID with fallback hash
    - DuplicateChecker → Redis NX SET; DB unique-index is the actual SoT
    - ConversationLock → per-PSID serialization, 60s TTL, Lua compare-and-delete

Backends are pluggable: pass a RedisClient + SupabaseClient (duck-typed) at construct time.
For unit tests we use the InMemoryRedis stub from v2.tests.conftest.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Protocol


# --- Protocols (so we can swap real/mock implementations) ---------------------

class RedisLike(Protocol):
    def set(self, key: str, value: str, *, nx: bool = False, ex: Optional[int] = None) -> bool: ...
    def get(self, key: str) -> Optional[str]: ...
    def delete(self, key: str) -> int: ...
    def eval(self, script: str, numkeys: int, *args: Any) -> Any: ...


# --- Public types -------------------------------------------------------------

@dataclass(frozen=True)
class MessageIdentity:
    platform: str
    raw_mid: Optional[str]
    full_id: str
    is_fallback: bool


@dataclass
class DuplicateCheckResult:
    is_duplicate: bool
    trace_id: str  # existing trace_id if duplicate, new trace_id if first time


# --- meta_message_id construction --------------------------------------------

def _short_hash(text: str, length: int = 12) -> str:
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def _attachments_hash(attachments: list) -> str:
    if not attachments:
        return ""
    urls = []
    for a in attachments:
        if isinstance(a, dict):
            payload = a.get("payload") or {}
            url = payload.get("url") or a.get("url") or ""
            if url:
                urls.append(url)
    return _short_hash("|".join(sorted(urls)))


def build_meta_message_id(event: dict, *, platform: str = "fb") -> MessageIdentity:
    """
    Build a stable platform-namespaced ID from a webhook event.

    Priority:
        1. event["message"]["mid"]  (Meta-issued, guaranteed unique)
        2. fallback hash: psid:timestamp:text_hash:attach_hash

    Returns MessageIdentity(full_id="fb:<id>", is_fallback=bool).
    """
    msg = event.get("message") or {}
    raw_mid = msg.get("mid")
    if raw_mid:
        return MessageIdentity(
            platform=platform,
            raw_mid=raw_mid,
            full_id=f"{platform}:{raw_mid}",
            is_fallback=False,
        )

    # Fallback
    psid = (event.get("sender") or {}).get("id") or ""
    timestamp = event.get("timestamp") or 0
    text = msg.get("text") or ""
    attachments = msg.get("attachments") or []

    text_hash = _short_hash(text) if text else ""
    attach_hash = _attachments_hash(attachments)

    fallback_raw = f"fallback:{psid}:{timestamp}:{text_hash}:{attach_hash}"
    return MessageIdentity(
        platform=platform,
        raw_mid=None,
        full_id=f"{platform}:{fallback_raw}",
        is_fallback=True,
    )


# --- Duplicate detection (Redis fast-path) ------------------------------------

class DuplicateChecker:
    """Redis-backed SETNX dedup. 24h TTL by default."""

    def __init__(self, redis: RedisLike, ttl_seconds: int = 86400):
        self.redis = redis
        self.ttl = ttl_seconds

    def _key(self, full_id: str) -> str:
        return f"idem:{full_id}"

    def check_duplicate_event(self, full_id: str) -> DuplicateCheckResult:
        """
        Atomic NX SET. If we win, return is_duplicate=False with new trace_id.
        If we lose, return is_duplicate=True with existing trace_id.
        """
        key = self._key(full_id)
        new_trace = str(uuid.uuid4())
        ok = self.redis.set(key, new_trace, nx=True, ex=self.ttl)
        if ok:
            return DuplicateCheckResult(is_duplicate=False, trace_id=new_trace)
        existing = self.redis.get(key) or ""
        return DuplicateCheckResult(is_duplicate=True, trace_id=existing)

    def force_clear(self, full_id: str) -> None:
        """Used when we explicitly want Meta to retry — e.g. after lock-timeout."""
        self.redis.delete(self._key(full_id))


# --- Per-PSID serialization lock ---------------------------------------------

# Lua compare-and-delete: only the lock owner can release it.
_LUA_CAD = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
else
  return 0
end
"""


class LockTimeoutError(RuntimeError):
    """Raised when conversation lock cannot be acquired within retries."""


class ConversationLock:
    """
    Per-PSID lock; default TTL 60s, exponential backoff up to 5 attempts.

    Usage:
        lock = ConversationLock(redis, "PSID_X", trace_id)
        if lock.acquire():
            try:
                ...
            finally:
                lock.release()
    """

    def __init__(
        self,
        redis: RedisLike,
        psid: str,
        trace_id: str,
        ttl_seconds: int = 60,
        max_retries: int = 5,
        base_backoff: float = 0.5,
        sleep_fn: Any = time.sleep,
    ):
        self.redis = redis
        self.psid = psid
        self.trace_id = trace_id
        self.ttl = ttl_seconds
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        self._sleep = sleep_fn
        self._key = f"lock:conversation:{psid}"
        self._value = f"{os.getpid()}:{trace_id}"
        self._acquired = False

    def acquire(self) -> bool:
        for attempt in range(self.max_retries):
            ok = self.redis.set(self._key, self._value, nx=True, ex=self.ttl)
            if ok:
                self._acquired = True
                return True
            self._sleep(self.base_backoff * (2 ** attempt))
        return False

    def acquire_or_raise(self) -> None:
        if not self.acquire():
            raise LockTimeoutError(
                f"Could not acquire lock for PSID={self.psid!r} after {self.max_retries} retries"
            )

    def release(self) -> bool:
        if not self._acquired:
            return False
        # Compare-and-delete via Lua
        result = self.redis.eval(_LUA_CAD, 1, self._key, self._value)
        self._acquired = False
        return bool(result)


# --- Convenience: top-level functions matching brief signatures --------------

def acquire_conversation_lock(
    redis: RedisLike, psid: str, trace_id: str, **kwargs
) -> ConversationLock:
    """Construct + acquire. Caller MUST call .release() in finally."""
    lock = ConversationLock(redis, psid, trace_id, **kwargs)
    lock.acquire_or_raise()
    return lock


def release_conversation_lock(lock: ConversationLock) -> bool:
    return lock.release()


def check_duplicate_event(
    redis: RedisLike, full_message_id: str, ttl_seconds: int = 86400
) -> DuplicateCheckResult:
    return DuplicateChecker(redis, ttl_seconds).check_duplicate_event(full_message_id)
