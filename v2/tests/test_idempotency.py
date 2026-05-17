"""Sprint 1 test: idempotency primitives."""

import uuid
import pytest
from v2.lib.idempotency import (
    build_meta_message_id,
    check_duplicate_event,
    acquire_conversation_lock,
    release_conversation_lock,
    ConversationLock,
    DuplicateChecker,
    LockTimeoutError,
)


class TestBuildMessageId:
    def test_with_mid(self):
        ev = {"sender": {"id": "PSID1"}, "timestamp": 1, "message": {"mid": "m_abc"}}
        identity = build_meta_message_id(ev)
        assert identity.platform == "fb"
        assert identity.raw_mid == "m_abc"
        assert identity.full_id == "fb:m_abc"
        assert identity.is_fallback is False

    def test_fallback_when_no_mid(self):
        ev = {"sender": {"id": "PSID1"}, "timestamp": 1736899200000,
              "message": {"text": "hi"}}
        identity = build_meta_message_id(ev)
        assert identity.is_fallback is True
        assert identity.full_id.startswith("fb:fallback:PSID1:")
        assert identity.raw_mid is None

    def test_fallback_stable(self):
        ev = {"sender": {"id": "PSID1"}, "timestamp": 1736899200000,
              "message": {"text": "hi"}}
        a = build_meta_message_id(ev)
        b = build_meta_message_id(ev)
        assert a.full_id == b.full_id

    def test_fallback_with_attachments(self):
        ev = {"sender": {"id": "PSID1"}, "timestamp": 1,
              "message": {"text": "", "attachments": [
                  {"payload": {"url": "https://x/y.jpg"}},
                  {"payload": {"url": "https://x/z.jpg"}},
              ]}}
        identity = build_meta_message_id(ev)
        assert identity.is_fallback is True
        # Different attachment list → different ID
        ev2 = {**ev, "message": {**ev["message"], "attachments": [
            {"payload": {"url": "https://x/z.jpg"}}
        ]}}
        identity2 = build_meta_message_id(ev2)
        assert identity.full_id != identity2.full_id


class TestDuplicateChecker:
    def test_first_time_not_duplicate(self, redis):
        result = check_duplicate_event(redis, "fb:m_1")
        assert result.is_duplicate is False
        assert uuid.UUID(result.trace_id)  # is valid uuid

    def test_replay_is_duplicate(self, redis):
        first = check_duplicate_event(redis, "fb:m_1")
        second = check_duplicate_event(redis, "fb:m_1")
        assert second.is_duplicate is True
        assert second.trace_id == first.trace_id

    def test_force_clear_allows_reprocess(self, redis):
        check_duplicate_event(redis, "fb:m_1")
        DuplicateChecker(redis).force_clear("fb:m_1")
        second = check_duplicate_event(redis, "fb:m_1")
        assert second.is_duplicate is False

    def test_different_ids_independent(self, redis):
        a = check_duplicate_event(redis, "fb:m_1")
        b = check_duplicate_event(redis, "fb:m_2")
        assert a.is_duplicate is False
        assert b.is_duplicate is False
        assert a.trace_id != b.trace_id


class TestConversationLock:
    def test_acquire_then_release(self, redis):
        lock = ConversationLock(redis, "PSID_X", "trace1", sleep_fn=lambda s: None)
        assert lock.acquire() is True
        # While locked, another can't acquire
        lock2 = ConversationLock(redis, "PSID_X", "trace2",
                                  max_retries=2, base_backoff=0,
                                  sleep_fn=lambda s: None)
        assert lock2.acquire() is False
        # Release
        assert lock.release() is True
        # Now another can acquire
        lock3 = ConversationLock(redis, "PSID_X", "trace3", sleep_fn=lambda s: None)
        assert lock3.acquire() is True
        lock3.release()

    def test_release_only_by_owner(self, redis):
        owner = ConversationLock(redis, "PSID_X", "trace1", sleep_fn=lambda s: None)
        owner.acquire()
        intruder = ConversationLock(redis, "PSID_X", "different_trace",
                                     sleep_fn=lambda s: None)
        intruder._acquired = True  # forge acquired state
        assert intruder.release() is False
        # Owner can still release
        assert owner.release() is True

    def test_acquire_or_raise(self, redis):
        # Pre-occupy the lock
        redis.set("lock:conversation:PSID_X", "other:value", ex=60)
        with pytest.raises(LockTimeoutError):
            acquire_conversation_lock(
                redis, "PSID_X", "trace1",
                max_retries=2, base_backoff=0, sleep_fn=lambda s: None,
            )

    def test_acquire_after_ttl_expiry(self, redis):
        # Lock expires immediately
        ConversationLock(
            redis, "PSID_X", "trace1", ttl_seconds=0, sleep_fn=lambda s: None
        ).acquire()
        # Next acquire should succeed because Redis evicted expired key
        # (InMemoryRedis honors TTL on access)
        lock = ConversationLock(
            redis, "PSID_X", "trace2", sleep_fn=lambda s: None
        )
        assert lock.acquire() is True


class TestIntegration:
    def test_dup_check_plus_lock_flow(self, redis):
        """Simulate full webhook entry: dedup then lock."""
        full_id = "fb:m_42"
        result = check_duplicate_event(redis, full_id)
        assert not result.is_duplicate

        lock = acquire_conversation_lock(
            redis, "PSID_A", result.trace_id,
            max_retries=2, base_backoff=0, sleep_fn=lambda s: None,
        )
        try:
            # ... simulated processing
            assert lock._acquired
        finally:
            release_conversation_lock(lock)

        # Replay → duplicate
        result2 = check_duplicate_event(redis, full_id)
        assert result2.is_duplicate
        assert result2.trace_id == result.trace_id
