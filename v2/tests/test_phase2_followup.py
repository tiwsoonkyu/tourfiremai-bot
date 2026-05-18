"""
Sprint 4 Phase 2 follow-up tests.

Closes three blockers from the Phase 2 live run:
  F1 — Cassette replay path was not wired into the corpus runner.
  F2 — cost_usd_estimate was always 0.0 despite real token usage.
  F3 — Regex false-positives on price-table columns dragged tip / deposit /
       single_supplement accuracy below the 85% goal.

All tests run mock-mode; no live OpenAI call.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from v2.lib.llm_pricing import (
    MODEL_PRICING_USD_PER_TOKEN, estimate_cost, format_cost, sum_costs,
)


# ---- F1: cassette replay wired ---------------------------------------------

class TestCassetteReplayWiring:
    """The runner must instantiate CassetteLLMClient (NOT MockLLMClient) when
    V2_STAGING_OPENAI_TEST_MODE=cassette or --replay-cassette is passed."""

    def _set_min_env(self, monkeypatch):
        monkeypatch.setenv("V2_STAGING_SUPABASE_URL", "http://x")
        monkeypatch.setenv("V2_STAGING_DB_HOST", "h")
        monkeypatch.setenv("V2_STAGING_DB_USER", "u")
        monkeypatch.setenv("V2_STAGING_DB_PASSWORD", "p")

    def test_env_var_cassette_mode_uses_cassette_client(self, monkeypatch, tmp_path):
        self._set_min_env(monkeypatch)
        monkeypatch.setenv("V2_STAGING_OPENAI_TEST_MODE", "cassette")
        # Set --cassette-dir so we don't accidentally rely on the default
        cassette_dir = tmp_path / "cassettes"
        cassette_dir.mkdir()

        from v2.scraper.run_fee_pipeline import main
        # Spy on what kind of client gets created by patching make_llm_client.
        captured: list = []
        import v2.scraper.run_fee_pipeline as runner
        from v2.lib.llm import CassetteLLMClient
        real = runner.make_llm_client
        def spy(config, *, cassette_dir=None):
            c = real(config, cassette_dir=cassette_dir)
            captured.append((config.openai_test_mode, type(c).__name__))
            return c
        monkeypatch.setattr(runner, "make_llm_client", spy)

        # Trigger the runner: --pdf-corpus-ondemand requires PDFs. Point the
        # runner at an empty tmp fixture tree so it short-circuits with rc=1
        # AFTER the LLM has been built.
        fix = tmp_path / "fixtures"
        for sub in ("pdfs/text_based", "pdfs/scanned", "pdfs/mixed", "ground_truth"):
            (fix / sub).mkdir(parents=True)
        real_join = runner.os.path.join
        def fake_join(a, *parts):
            j = real_join(a, *parts)
            if j.endswith("v2/tests/fixtures") or j.endswith("tests/fixtures"):
                return str(fix)
            return j
        monkeypatch.setattr(runner.os.path, "join", fake_join)

        rc = main(["--pdf-corpus-ondemand", "--cassette-dir", str(cassette_dir)])
        assert captured, "make_llm_client never called"
        mode, cls_name = captured[-1]
        assert mode == "cassette", f"expected mode=cassette, got {mode}"
        assert cls_name == "CassetteLLMClient", f"expected CassetteLLMClient, got {cls_name}"

    def test_explicit_replay_flag_uses_cassette_client(self, monkeypatch, tmp_path):
        self._set_min_env(monkeypatch)
        monkeypatch.delenv("V2_STAGING_OPENAI_TEST_MODE", raising=False)
        cassette_dir = tmp_path / "cassettes"
        cassette_dir.mkdir()

        from v2.scraper.run_fee_pipeline import main
        import v2.scraper.run_fee_pipeline as runner
        captured: list = []
        real = runner.make_llm_client
        def spy(config, *, cassette_dir=None):
            c = real(config, cassette_dir=cassette_dir)
            captured.append((config.openai_test_mode, type(c).__name__))
            return c
        monkeypatch.setattr(runner, "make_llm_client", spy)

        fix = tmp_path / "fixtures"
        for sub in ("pdfs/text_based", "pdfs/scanned", "pdfs/mixed", "ground_truth"):
            (fix / sub).mkdir(parents=True)
        real_join = runner.os.path.join
        def fake_join(a, *parts):
            j = real_join(a, *parts)
            if j.endswith("v2/tests/fixtures") or j.endswith("tests/fixtures"):
                return str(fix)
            return j
        monkeypatch.setattr(runner.os.path, "join", fake_join)

        main(["--pdf-corpus-ondemand", "--replay-cassette", "--cassette-dir", str(cassette_dir)])
        assert captured, "make_llm_client never called"
        mode, cls_name = captured[-1]
        assert mode == "cassette"
        assert cls_name == "CassetteLLMClient"

    def test_replay_path_makes_no_network_call(self, monkeypatch, tmp_path):
        """Defense-in-depth: CassetteLLMClient must NOT import or call openai."""
        from v2.lib.cassette_redactor import redact_cassette
        from v2.lib.llm import CassetteLLMClient, LLMResponse, LLMUsage
        import json
        cassette_dir = tmp_path / "cassettes"
        cassette_dir.mkdir()
        # Build a cassette by hand
        client = CassetteLLMClient(str(cassette_dir), config=type("C", (), {})())
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}]
        rf = {"type": "json_schema", "json_schema": {"name": "TourFees", "strict": True}}
        h = CassetteLLMClient._hash_request("fast", msgs, rf)
        cassette_path = cassette_dir / f"{h}.json"
        cassette_path.write_text(json.dumps({
            "request": {"tier": "fast", "messages": msgs, "response_format": rf},
            "response": {"text": "{}", "structured": {"extraction_confidence": 0.9},
                          "finish_reason": "stop",
                          "usage": {"tokens_in": 10, "tokens_out": 5,
                                     "cost_usd_estimate": 0.0,
                                     "model_used": "gpt-5-nano", "latency_ms": 100}},
        }))

        # Block any HTTP attempt (only patch modules that are importable)
        patches = []
        try:
            import requests  # noqa
            patches.append(patch("requests.get", side_effect=RuntimeError("network forbidden")))
            patches.append(patch("requests.post", side_effect=RuntimeError("network forbidden")))
        except ImportError:
            pass
        try:
            import openai  # type: ignore  # noqa
            patches.append(patch("openai.OpenAI", side_effect=RuntimeError("network forbidden")))
        except ImportError:
            pass
        # Activate all patches
        from contextlib import ExitStack
        with ExitStack() as stack:
            for ptch in patches:
                stack.enter_context(ptch)
            rsp = client.chat(tier="fast", messages=msgs, response_format=rf)
        assert rsp.cassette_hit is True
        assert rsp.usage.tokens_in == 10


# ---- F2: cost reporting ----------------------------------------------------

class TestLLMPricing:
    def test_known_model_returns_nonzero_cost(self):
        cost = estimate_cost("gpt-4o", tokens_in=1000, tokens_out=500)
        assert cost is not None
        assert cost > 0
        # Sanity: gpt-4o is $0.0025/in + $0.01/out → 1000*0.0025 + 500*0.01 = 7.5
        assert cost == pytest.approx(7.5, rel=0.01)

    def test_unknown_model_returns_none(self):
        cost = estimate_cost("gpt-99-not-a-real-model", tokens_in=100, tokens_out=100)
        assert cost is None

    def test_none_model_returns_none(self):
        assert estimate_cost(None, 100, 100) is None
        assert estimate_cost("", 100, 100) is None

    def test_zero_tokens_returns_zero(self):
        assert estimate_cost("gpt-5-nano", 0, 0) == 0.0

    def test_format_cost_dollar_string(self):
        assert format_cost(0.0123) == "$0.0123"

    def test_format_cost_unknown(self):
        assert format_cost(None) == "unknown"

    def test_sum_costs_all_known(self):
        total, unknown = sum_costs([0.001, 0.002, 0.003])
        assert total == pytest.approx(0.006)
        assert unknown == 0

    def test_sum_costs_some_unknown(self):
        total, unknown = sum_costs([0.001, None, 0.003])
        # When some are unknown but at least one is priced, total is a lower bound
        assert total == pytest.approx(0.004)
        assert unknown == 1

    def test_sum_costs_all_unknown(self):
        total, unknown = sum_costs([None, None])
        assert total is None
        assert unknown == 2


class TestOpenAILLMClientCostPlumbing:
    """The OpenAI live client must populate cost_usd_estimate when the model
    is in the pricing table."""

    def test_chat_populates_cost(self):
        from v2.lib.llm import OpenAILLMClient, LLMResponse
        # Build a fake config dataclass-shaped object.
        cfg = type("C", (), {
            "openai_api_key": "sk-test",
            "openai_response_model": "gpt-4o",
            "openai_fast_model": "gpt-4o-mini",
            "openai_vision_model": "gpt-4o",
            "openai_max_retries": 1,
            "openai_timeout_sec": 5,
        })()
        client = OpenAILLMClient(cfg)
        # Fake the underlying SDK call
        class _FakeRsp:
            class _C:
                class _M:
                    content = "{}"
                message = _M()
                finish_reason = "stop"
            choices = [_C()]
            class _U:
                prompt_tokens = 1000
                completion_tokens = 500
            usage = _U()
        class _FakeClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kwargs):
                        return _FakeRsp()
        client._client = _FakeClient()
        rsp = client.chat(tier="response", messages=[{"role": "user", "content": "x"}])
        # gpt-4o (response tier default): 1000 in * 0.0025 + 500 out * 0.01 = 7.5
        assert rsp.usage.cost_usd_estimate == pytest.approx(7.5, rel=0.01)
        assert rsp.usage.model_used == "gpt-4o"

    def test_chat_unknown_model_keeps_zero(self):
        """Backwards-compat: unknown model → cost stays 0.0 in LLMUsage shape
        (so cassettes don't break). Callers can re-call estimate_cost() with
        usage.model_used to get the explicit `None` signal."""
        from v2.lib.llm import OpenAILLMClient
        cfg = type("C", (), {
            "openai_api_key": "sk-test",
            "openai_response_model": "gpt-99-mystery",
            "openai_fast_model": "gpt-99-mystery",
            "openai_vision_model": "gpt-99-mystery",
            "openai_max_retries": 1,
            "openai_timeout_sec": 5,
        })()
        client = OpenAILLMClient(cfg)
        class _FakeRsp:
            class _C:
                class _M: content = "{}"
                message = _M(); finish_reason = "stop"
            choices = [_C()]
            class _U:
                prompt_tokens = 100; completion_tokens = 100
            usage = _U()
        class _FakeClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kwargs): return _FakeRsp()
        client._client = _FakeClient()
        rsp = client.chat(tier="response", messages=[{"role": "user", "content": "x"}])
        assert rsp.usage.cost_usd_estimate == 0.0
        # But model_used is recorded — caller can re-resolve via estimate_cost.
        assert rsp.usage.model_used == "gpt-99-mystery"
        assert estimate_cost(rsp.usage.model_used,
                              rsp.usage.tokens_in, rsp.usage.tokens_out) is None


# ---- F3: regex anti-false-positive + merge ---------------------------------

class TestRegexRequiresBahtSuffix:
    """Money-critical regex now requires a 'บาท'/'baht' suffix near the value.
    This kills the price-table column false positives."""

    def test_tip_with_baht_suffix_matches(self):
        from v2.scraper.extract_fees import regex_extract
        text = "ค่าทิปไกด์และคนขับรถ ท่านละ 2,000 บาท"
        r = regex_extract(text)
        assert r.tip_amount == 2000
        assert r.tip_confidence == 0.85

    def test_tip_without_baht_does_not_match(self):
        from v2.scraper.extract_fees import regex_extract
        # Number without บาท suffix — common in price tables
        text = "ทิป 19 – 23 มิถุนายน 2569"  # only date numbers, no บาท
        r = regex_extract(text)
        assert r.tip_amount is None

    def test_single_supplement_table_row_does_not_false_positive(self):
        from v2.scraper.extract_fees import regex_extract
        # Real WS01 pattern: "ห้องพักเดี่ยว ท่าน 19 – 23 มิถุนายน 2569 19,990 19,990 15,990 6,000"
        # Old regex would capture 19,990 wrongly. New regex must yield NULL
        # because no บาท follows the column numbers.
        text = ("อัตราค่าบริการ ห้องพักเดี่ยว ท่าน "
                "19 – 23 มิถุนายน 2569 19,990 19,990 15,990 6,000")
        r = regex_extract(text)
        assert r.single_supplement is None, \
            f"expected NULL but got {r.single_supplement}"

    def test_single_supplement_fee_line_with_baht_matches(self):
        from v2.scraper.extract_fees import regex_extract
        text = "พักเดี่ยวเพิ่ม 5,500 บาท"
        r = regex_extract(text)
        assert r.single_supplement == 5500
        assert r.single_supplement_confidence == 0.82

    def test_deposit_with_thai_context_matches(self):
        from v2.scraper.extract_fees import regex_extract
        text = "กรุณาชำระเงินมัดจำ ท่านละ 15,000 บาท ภายใน 7 วัน"
        r = regex_extract(text)
        assert r.deposit_amount == 15000
        assert r.deposit_confidence == 0.85

    def test_deposit_in_price_table_does_not_match(self):
        from v2.scraper.extract_fees import regex_extract
        # Common WS03/04/05 failure mode: bare number near "deposit" keyword
        # mention in a description page, but no บาท suffix.
        text = "deposit 19890 ห้องพัก"  # malformed, no baht
        r = regex_extract(text)
        assert r.deposit_amount is None


class TestMergePrefersHigherConfidence:
    """When two layers disagree on a money-critical field, the higher
    per-field confidence wins. Old behavior was 'primary keeps wrong value'."""

    def test_vision_overrides_regex_when_higher_confidence(self):
        from v2.scraper.extract_fees import ExtractionResult, _merge_results
        # Simulates: regex captured 19 (wrong) at 0.82, vision says 6000 at 0.92
        primary = ExtractionResult(
            single_supplement=19, single_supplement_confidence=0.82,
            extraction_method="pdfplumber+regex",
        )
        other = ExtractionResult(
            single_supplement=6000, single_supplement_confidence=0.92,
            extraction_method="llm_vision",
        )
        merged = _merge_results(primary, other)
        assert merged.single_supplement == 6000
        assert merged.single_supplement_confidence == 0.92

    def test_lower_confidence_does_not_override(self):
        from v2.scraper.extract_fees import ExtractionResult, _merge_results
        primary = ExtractionResult(
            tip_amount=2000, tip_confidence=0.85,
        )
        other = ExtractionResult(
            tip_amount=9999, tip_confidence=0.50,
        )
        merged = _merge_results(primary, other)
        assert merged.tip_amount == 2000
        assert merged.tip_confidence == 0.85

    def test_null_filled_from_other(self):
        from v2.scraper.extract_fees import ExtractionResult, _merge_results
        primary = ExtractionResult(deposit_amount=None)
        other = ExtractionResult(deposit_amount=10000, deposit_confidence=0.85)
        merged = _merge_results(primary, other)
        assert merged.deposit_amount == 10000
        assert merged.deposit_confidence == 0.85


# ---- Anti-guess invariant still holds --------------------------------------

class TestAntiGuessInvariantPreserved:
    """Even though regex baselines went up, single_supplement still requires
    confidence ≥ 0.90 to answer per the policy. A regex-only 0.82 value must
    still produce a handoff."""

    def test_single_supplement_at_0_82_still_handsoff(self):
        from v2.lib.fee_answer_policy import decide_fee_answer
        row = {
            "single_supplement": 5500,
            "single_supplement_confidence": 0.82,
        }
        d = decide_fee_answer(row, "single_supplement")
        assert d.decision == "handoff_low_confidence"
        assert d.threshold == 0.90

    def test_tip_at_0_85_answers(self):
        from v2.lib.fee_answer_policy import decide_fee_answer
        row = {"tip_amount": 2000, "tip_confidence": 0.85}
        d = decide_fee_answer(row, "tip")
        assert d.decision == "answer"
        assert d.value == 2000
