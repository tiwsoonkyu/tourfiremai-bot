"""Sprint 3 test: LLM client modes (mock, cassette) — NO live calls."""

import os
import json
import pytest

from v2.lib.llm import (
    LLMResponse, LLMUsage, MockLLMClient, CassetteLLMClient,
    CassetteMissError, make_llm_client, _FALLBACK_LADDERS,
)


# --- Mock client tests --------------------------------------------------------

class TestMockClient:
    def test_response_tier_greeting_state(self):
        c = MockLLMClient()
        rsp = c.chat(
            tier="response",
            messages=[{"role": "system", "content": "system prompt"},
                      {"role": "user", "content": "state=new_lead\nintent=greeting"}],
        )
        assert isinstance(rsp, LLMResponse)
        assert "สวัสดี" in rsp.text
        assert rsp.usage.model_used == "mock:response"
        assert rsp.cassette_hit is False
        assert rsp.mock_decision == "greet"

    def test_response_tier_options_presented(self):
        c = MockLLMClient()
        rsp = c.chat(
            tier="response",
            messages=[{"role": "user", "content": "state=options_presented\n[present_top_3]"}],
        )
        assert "3 โปรแกรม" in rsp.text or "Top" in rsp.text or "เลือก" in rsp.text
        assert rsp.mock_decision == "present_top_n"

    def test_response_tier_fee_complete(self):
        c = MockLLMClient()
        rsp = c.chat(
            tier="response",
            messages=[{"role": "user", "content": "state=fee_check_required\n[fee_complete=True]"}],
        )
        # mock returns canned fee summary with ทิป/วีซ่า keywords
        assert "ทิป" in rsp.text or "วีซ่า" in rsp.text or "พักเดี่ยว" in rsp.text

    def test_fast_tier_with_structured_format(self):
        c = MockLLMClient()
        rsp = c.chat(
            tier="fast",
            messages=[{"role": "user", "content": "x"}],
            response_format={"type": "json_schema",
                              "json_schema": {"name": "Foo", "strict": True}},
        )
        assert rsp.structured is not None
        assert "intent_refined" in rsp.structured
        assert rsp.usage.model_used == "mock:fast"

    def test_vision_tier_returns_skeleton(self):
        c = MockLLMClient()
        rsp = c.vision(messages=[{"role": "user", "content": "x"}],
                        image_bytes=b"fake-image-bytes",
                        response_format={"type": "json_schema", "json_schema": {"name": "TourFees"}})
        assert rsp.structured is not None
        assert "tip_amount" in rsp.structured
        # Skeleton fees are all None
        assert rsp.structured["tip_amount"] is None
        assert rsp.usage.model_used == "mock:vision"

    def test_call_log_captures_invocations(self):
        c = MockLLMClient()
        c.chat(tier="fast", messages=[{"role": "user", "content": "hello"}])
        c.chat(tier="response", messages=[{"role": "user", "content": "state=new_lead"}])
        assert len(c.call_log) == 2
        assert c.call_log[0]["tier"] == "fast"
        assert c.call_log[1]["tier"] == "response"


# --- Cassette client tests ----------------------------------------------------

class TestCassetteClient:
    def test_missing_cassette_raises_strict(self, tmp_path):
        c = CassetteLLMClient(str(tmp_path), strict=True)
        with pytest.raises(CassetteMissError):
            c.chat(tier="response", messages=[{"role": "user", "content": "x"}])

    def test_missing_cassette_falls_back_to_mock_when_lenient(self, tmp_path):
        c = CassetteLLMClient(str(tmp_path), strict=False)
        rsp = c.chat(tier="response",
                     messages=[{"role": "user", "content": "state=new_lead\nintent=greeting"}])
        assert "สวัสดี" in rsp.text

    def test_cassette_hit_replays_response(self, tmp_path):
        c = CassetteLLMClient(str(tmp_path), strict=True)
        # Pre-write a cassette
        messages = [{"role": "user", "content": "test prompt"}]
        h = CassetteLLMClient._hash_request("response", messages, None)
        cassette = {
            "request": {"tier": "response", "messages": messages},
            "response": {
                "text": "replayed cassette text",
                "usage": {"tokens_in": 100, "tokens_out": 50, "model_used": "gpt-5.1", "latency_ms": 1000},
            },
        }
        with open(os.path.join(tmp_path, f"{h}.json"), "w") as f:
            json.dump(cassette, f)

        rsp = c.chat(tier="response", messages=messages)
        assert rsp.text == "replayed cassette text"
        assert rsp.cassette_hit is True
        assert rsp.usage.tokens_in == 100


# --- Factory tests ------------------------------------------------------------

class TestFactory:
    def test_make_mock(self):
        from types import SimpleNamespace
        cfg = SimpleNamespace(openai_test_mode="mock", openai_api_key=None,
                               openai_response_model="x", openai_fast_model="y",
                               openai_vision_model="z", openai_max_retries=3,
                               openai_timeout_sec=30)
        c = make_llm_client(cfg)
        assert isinstance(c, MockLLMClient)

    def test_make_cassette(self, tmp_path):
        from types import SimpleNamespace
        cfg = SimpleNamespace(openai_test_mode="cassette", openai_api_key=None,
                               openai_response_model="x", openai_fast_model="y",
                               openai_vision_model="z", openai_max_retries=3,
                               openai_timeout_sec=30)
        c = make_llm_client(cfg, cassette_dir=str(tmp_path))
        assert isinstance(c, CassetteLLMClient)

    def test_make_live_requires_key(self):
        from types import SimpleNamespace
        cfg = SimpleNamespace(openai_test_mode="live", openai_api_key=None,
                               openai_response_model="x", openai_fast_model="y",
                               openai_vision_model="z", openai_max_retries=3,
                               openai_timeout_sec=30)
        c = make_llm_client(cfg)
        # Should fail when we try to use it (lazy)
        from v2.lib.llm import OpenAILLMClient
        assert isinstance(c, OpenAILLMClient)
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            c.chat(tier="response", messages=[{"role": "user", "content": "x"}])

    def test_invalid_mode_raises(self):
        from types import SimpleNamespace
        cfg = SimpleNamespace(openai_test_mode="bogus", openai_api_key=None,
                               openai_response_model="x", openai_fast_model="y",
                               openai_vision_model="z", openai_max_retries=3,
                               openai_timeout_sec=30)
        with pytest.raises(ValueError):
            make_llm_client(cfg)


# --- Fallback ladder structure ------------------------------------------------

class TestFallbackLadders:
    def test_response_ladder_starts_with_5x(self):
        # We can't test runtime ladder without live client, but verify const
        assert "gpt-5.1" in _FALLBACK_LADDERS["response"]
        assert "gpt-4o" in _FALLBACK_LADDERS["response"]  # eventual fallback

    def test_fast_ladder_includes_mini(self):
        assert "gpt-5-nano" in _FALLBACK_LADDERS["fast"]
        assert "gpt-4o-mini" in _FALLBACK_LADDERS["fast"]

    def test_vision_ladder_includes_4o(self):
        assert "gpt-4o" in _FALLBACK_LADDERS["vision"]
