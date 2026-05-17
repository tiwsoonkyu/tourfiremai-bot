"""Sprint 4 test: RecordingLLMClient — wraps live and persists cassettes."""

import json
import os
import pytest

from v2.lib.llm import (
    RecordingLLMClient, MockLLMClient, CassetteLLMClient,
    LLMResponse, LLMUsage,
)


class FakeLiveClient:
    """Pretends to be OpenAILLMClient — returns canned LLMResponse."""
    def __init__(self):
        self.chat_calls = 0
        self.vision_calls = 0
    def chat(self, *, tier, messages, response_format=None, max_tokens=None, temperature=None):
        self.chat_calls += 1
        return LLMResponse(
            text="live response text",
            structured={"tip_amount": 1500},
            finish_reason="stop",
            usage=LLMUsage(tokens_in=100, tokens_out=20,
                           model_used="gpt-5.1", latency_ms=420,
                           cost_usd_estimate=0.0002),
        )
    def vision(self, *, messages, image_bytes, response_format=None, max_tokens=None):
        self.vision_calls += 1
        return LLMResponse(
            text="vision response",
            structured={"tip_amount": 999},
            usage=LLMUsage(tokens_in=300, tokens_out=80,
                           model_used="gpt-4o", latency_ms=1200),
        )


def _noop_redactor(d): return d


class TestRecording:
    def test_chat_calls_live_and_writes_cassette(self, tmp_path):
        live = FakeLiveClient()
        rec = RecordingLLMClient(live, str(tmp_path), redactor=_noop_redactor)
        rsp = rec.chat(tier="fast", messages=[{"role": "user", "content": "hello"}])

        assert rsp.text == "live response text"
        assert live.chat_calls == 1

        # Cassette file written
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["request"]["tier"] == "fast"
        assert data["response"]["text"] == "live response text"
        assert data["response"]["structured"]["tip_amount"] == 1500

    def test_replay_via_cassette_client_after_recording(self, tmp_path):
        live = FakeLiveClient()
        rec = RecordingLLMClient(live, str(tmp_path), redactor=_noop_redactor)
        msgs = [{"role": "user", "content": "replay-me"}]
        rec.chat(tier="fast", messages=msgs)

        replay = CassetteLLMClient(str(tmp_path), strict=True)
        rsp = replay.chat(tier="fast", messages=msgs)
        assert rsp.cassette_hit
        assert rsp.text == "live response text"
        # Live client NOT called for replay
        assert live.chat_calls == 1

    def test_vision_records_with_image_hash(self, tmp_path):
        live = FakeLiveClient()
        rec = RecordingLLMClient(live, str(tmp_path), redactor=_noop_redactor)
        img = b"fake-png-bytes-content"
        rec.vision(messages=[{"role": "user", "content": "x"}], image_bytes=img)

        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text(encoding="utf-8"))
        # Cassette messages include an "image" role entry
        roles = [m.get("role") for m in data["request"]["messages"]]
        assert "image" in roles

    def test_redactor_applied(self, tmp_path):
        called = {"n": 0}
        def my_redactor(d):
            called["n"] += 1
            return {**d, "redacted_flag": True}

        live = FakeLiveClient()
        rec = RecordingLLMClient(live, str(tmp_path), redactor=my_redactor)
        rec.chat(tier="fast", messages=[{"role": "user", "content": "x"}])

        assert called["n"] == 1
        files = list(tmp_path.glob("*.json"))
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data.get("redacted_flag") is True

    def test_redactor_failure_doesnt_block_write(self, tmp_path):
        def broken_redactor(d): raise RuntimeError("boom")
        live = FakeLiveClient()
        rec = RecordingLLMClient(live, str(tmp_path), redactor=broken_redactor)
        # Should NOT raise; cassette still written
        rec.chat(tier="fast", messages=[{"role": "user", "content": "x"}])
        assert len(list(tmp_path.glob("*.json"))) == 1

    def test_meta_block_present(self, tmp_path):
        live = FakeLiveClient()
        rec = RecordingLLMClient(live, str(tmp_path), redactor=_noop_redactor,
                                   meta={"git_commit": "abc123", "pdf_basename": "x.pdf"})
        rec.chat(tier="fast", messages=[{"role": "user", "content": "y"}])
        files = list(tmp_path.glob("*.json"))
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["meta"]["git_commit"] == "abc123"
        assert data["meta"]["pdf_basename"] == "x.pdf"
        assert "recorded_at" in data["meta"]
