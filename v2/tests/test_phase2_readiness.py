"""
Sprint 4 Phase 2 readiness tests.

Closes the two blockers Codex identified before live PDF vision recording:
  - The vision/text response_format must carry a complete strict JSON schema
    (not just `{name, strict}` with no `schema` body) — otherwise OpenAI
    strict mode can silently relax or fail.
  - `run_fee_pipeline --pdf-corpus` measured the Sprint 3 per-page path
    (`extract_fees_per_page`), not the Sprint 4 follow-up runtime path
    (`extract_fees_on_demand`). Phase 2 must measure the path the bot uses.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from v2.lib.fee_schema import (
    TOUR_FEES_JSON_SCHEMA, TOUR_FEES_REQUIRED_FIELDS, build_response_format,
)


# ---- Schema completeness ---------------------------------------------------

class TestFeeJsonSchemaCompleteness:
    """Lock in that the schema body is actually transported to OpenAI."""

    def test_schema_has_required_top_level_keys(self):
        assert TOUR_FEES_JSON_SCHEMA["type"] == "object"
        assert TOUR_FEES_JSON_SCHEMA["additionalProperties"] is False
        assert isinstance(TOUR_FEES_JSON_SCHEMA["required"], list)
        assert isinstance(TOUR_FEES_JSON_SCHEMA["properties"], dict)

    def test_all_13_required_fields_present(self):
        expected = {
            "tip_amount", "visa_fee", "visa_status",
            "single_supplement", "infant_fee", "child_fee_no_bed",
            "deposit_amount", "joinland_price",
            "mandatory_fees_summary",
            "extraction_confidence", "source_page", "raw_snippet", "notes",
        }
        assert set(TOUR_FEES_REQUIRED_FIELDS) == expected
        assert set(TOUR_FEES_JSON_SCHEMA["required"]) == expected
        # Every required key must have a property entry.
        for f in expected:
            assert f in TOUR_FEES_JSON_SCHEMA["properties"], f"missing {f}"

    def test_numeric_fields_are_nullable_integer(self):
        for field in ("tip_amount", "visa_fee", "single_supplement",
                       "infant_fee", "child_fee_no_bed", "deposit_amount",
                       "joinland_price", "source_page"):
            entry = TOUR_FEES_JSON_SCHEMA["properties"][field]
            assert entry["type"] == ["integer", "null"], f"{field}: {entry['type']}"

    def test_visa_status_enum_allows_known_values_and_null(self):
        entry = TOUR_FEES_JSON_SCHEMA["properties"]["visa_status"]
        assert entry["type"] == ["string", "null"]
        for v in ("exempt", "required", "on_arrival", "evisa", "unknown"):
            assert v in entry["enum"]
        assert None in entry["enum"]

    def test_extraction_confidence_is_bounded_number(self):
        entry = TOUR_FEES_JSON_SCHEMA["properties"]["extraction_confidence"]
        assert entry["type"] == "number"
        assert entry["minimum"] == 0
        assert entry["maximum"] == 1

    def test_no_additional_properties(self):
        # Defensive: if a typo adds an unexpected property, strict mode rejects.
        assert TOUR_FEES_JSON_SCHEMA["additionalProperties"] is False


class TestBuildResponseFormat:
    """The helper must produce a fully-formed OpenAI response_format dict."""

    def test_default_returns_strict_tourfees(self):
        rf = build_response_format()
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["name"] == "TourFees"
        assert rf["json_schema"]["strict"] is True
        # Schema body is transported, not just {name, strict}
        assert "schema" in rf["json_schema"]
        assert rf["json_schema"]["schema"] is TOUR_FEES_JSON_SCHEMA

    def test_schema_body_is_complete(self):
        rf = build_response_format()
        schema = rf["json_schema"]["schema"]
        assert "properties" in schema
        assert "required" in schema
        assert "additionalProperties" in schema
        # Sanity: properties dict is non-trivial.
        assert len(schema["properties"]) >= 13

    def test_strict_can_be_overridden(self):
        rf = build_response_format(strict=False)
        assert rf["json_schema"]["strict"] is False


class TestProductionCallSitesUseFullSchema:
    """
    The three production call sites (llm_text_extract, llm_vision_extract,
    extract_fees_on_demand) must pass the FULL schema body, not the partial
    dict that was there before this patch. We verify by inspecting the
    `response_format` recorded by the MockLLMClient at each call site.
    """

    def test_llm_text_extract_passes_full_schema(self):
        from v2.lib.llm import MockLLMClient
        from v2.scraper.extract_fees import llm_text_extract

        llm = MockLLMClient()
        text = (
            "tip 1500 baht\ndeposit 10000 baht\nsingle supplement 5500 baht\n"
            "visa exempt for thai\n"
        )
        llm_text_extract(text, llm)
        assert llm.call_log, "MockLLMClient was not called"
        last = llm.call_log[-1]
        assert last["tier"] == "fast"
        assert last["response_format"] is True  # MockLLMClient records bool
        # Inspect the actual arg via a separate spy
        captured: list[dict] = []
        class _Spy(MockLLMClient):
            def chat(self, *, tier, messages, response_format=None,
                     max_tokens=None, temperature=None):
                captured.append(response_format)
                return super().chat(tier=tier, messages=messages,
                                      response_format=response_format,
                                      max_tokens=max_tokens, temperature=temperature)
        llm_text_extract(text, _Spy())
        assert captured, "spy did not intercept response_format"
        rf = captured[-1]
        assert rf["type"] == "json_schema"
        js = rf["json_schema"]
        assert js["name"] == "TourFees"
        assert js["strict"] is True
        assert "schema" in js, "schema body must be transported to OpenAI"
        assert "properties" in js["schema"]
        assert "tip_amount" in js["schema"]["properties"]

    def test_extract_fees_on_demand_vision_passes_full_schema(self, tmp_path):
        """Trigger the vision call inside extract_fees_on_demand and capture
        the response_format dict sent to llm.vision()."""
        from v2.lib.llm import MockLLMClient, LLMResponse, LLMUsage
        from v2.tests.fixtures.synthetic_pdf import build_synthetic_fee_pdf
        from v2.scraper import ondemand_vision
        from v2.lib.cache import _InMemoryRedis

        body = (
            "Tour Fee Schedule:\n"
            "tip 1500 baht\ndeposit 10000 baht\n"
            "single supplement 5500 baht\nvisa exempt\n"
        )
        pdf_path = str(tmp_path / "synth.pdf")
        build_synthetic_fee_pdf(pdf_path, fee_text=body)

        captured: list[dict] = []
        class _Spy(MockLLMClient):
            def vision(self, *, messages, image_bytes, response_format=None,
                        max_tokens=None, temperature=None):
                captured.append(response_format)
                return LLMResponse(
                    text="{}",
                    structured={
                        "tip_amount": 1500, "deposit_amount": 10000,
                        "single_supplement": 5500, "visa_status": "exempt",
                        "extraction_confidence": 0.9, "source_page": 1,
                    },
                    finish_reason="stop",
                    usage=LLMUsage(tokens_in=10, tokens_out=10,
                                     cost_usd_estimate=0.0,
                                     model_used="gpt-4o", latency_ms=100),
                )

        class _Img:
            def save(self, buf, format):
                buf.write(b"\x89PNG\r\n\x1a\n")

        with patch.object(ondemand_vision, "vision_available", return_value=(True, None)), \
             patch("pdf2image.convert_from_path", return_value=[_Img()]):
            ondemand_vision.extract_fees_on_demand(
                pdf_path, _Spy(),
                pdf_hash="readyz_aa" * 6,
                cache=_InMemoryRedis(),
                max_vision_pages=1,
            )

        assert captured, "vision spy did not intercept response_format"
        rf = captured[-1]
        assert rf["type"] == "json_schema"
        js = rf["json_schema"]
        assert js["name"] == "TourFees"
        assert js["strict"] is True
        assert "schema" in js
        assert "single_supplement" in js["schema"]["properties"]
        assert "additionalProperties" in js["schema"]
        assert js["schema"]["additionalProperties"] is False


# ---- Corpus runner exercises extract_fees_on_demand -----------------------

class TestCorpusOnDemandRunner:
    """
    `run_fee_pipeline --pdf-corpus-ondemand` must drive
    `extract_fees_on_demand` once per (PDF × asked_field). Verify this via a
    spy that replaces the on-demand entry point.
    """

    def _make_two_fixture_pdfs(self, tmp_path):
        from v2.tests.fixtures.synthetic_pdf import build_synthetic_fee_pdf
        # We can't write to v2/tests/fixtures/pdfs from the test, so we copy
        # the fixture dir contents to a tmp scaffold and point the runner at it
        # via monkeypatch on the fixture_root resolution.
        body = (
            "Tour Fee Schedule:\n"
            "tip 1500 baht\ndeposit 10000 baht\n"
            "single supplement 5500 baht\nvisa exempt\n"
        )
        d = tmp_path / "fixtures" / "pdfs" / "text_based"
        d.mkdir(parents=True)
        gtd = tmp_path / "fixtures" / "ground_truth"
        gtd.mkdir(parents=True)
        # 2 PDFs to exercise per-PDF iteration
        p1 = d / "WS_one.pdf"
        p2 = d / "WS_two.pdf"
        build_synthetic_fee_pdf(str(p1), fee_text=body)
        build_synthetic_fee_pdf(str(p2), fee_text=body)
        # Also create the scanned/ + mixed/ subdirs so glob doesn't error.
        (tmp_path / "fixtures" / "pdfs" / "scanned").mkdir(parents=True)
        (tmp_path / "fixtures" / "pdfs" / "mixed").mkdir(parents=True)
        return tmp_path / "fixtures"

    def test_corpus_ondemand_invokes_extract_fees_on_demand_per_asked_field(
        self, tmp_path, monkeypatch
    ):
        fixture_root = self._make_two_fixture_pdfs(tmp_path)
        # Force the runner to use our tmp fixture tree.
        monkeypatch.setenv("V2_STAGING_SUPABASE_URL", "http://x")
        monkeypatch.setenv("V2_STAGING_DB_HOST", "h")
        monkeypatch.setenv("V2_STAGING_DB_USER", "u")
        monkeypatch.setenv("V2_STAGING_DB_PASSWORD", "p")

        from v2.scraper import run_fee_pipeline as pipeline_mod

        # Monkeypatch os.path.abspath logic: we override the fixture_root path
        # by patching the module-level computation via a small shim.
        real_join = pipeline_mod.os.path.join
        def fake_join(a, *parts):
            # When the runner builds the fixture path, return our tmp tree.
            joined = real_join(a, *parts)
            if joined.endswith("v2/tests/fixtures") or joined.endswith("tests/fixtures"):
                return str(fixture_root)
            return joined
        monkeypatch.setattr(pipeline_mod.os.path, "join", fake_join)

        # Spy on extract_fees_on_demand inside the runner's module.
        calls: list[dict] = []
        from v2.scraper.ondemand_vision import (
            extract_fees_on_demand as real_od, OnDemandResult,
        )
        from v2.scraper.extract_fees import ExtractionResult
        def _spy_od(pdf_path, llm, *, pdf_hash, prior=None, cache=None,
                     max_vision_pages=3, asked_field=None,
                     extraction_version="1.0"):
            calls.append({
                "pdf_path": pdf_path, "pdf_hash": pdf_hash,
                "asked_field": asked_field,
                "max_vision_pages": max_vision_pages,
                "extraction_version": extraction_version,
                "cache_provided": cache is not None,
                "prior_provided": prior is not None,
            })
            return OnDemandResult(
                result=prior or ExtractionResult(extraction_method="none"),
                cache_hit=False, cache_key="k",
                candidate_pages=[1], vision_pages_used=0,
                ocr_available=True, skipped_reason=None,
            )

        # Patch in the run_fee_pipeline module's namespace (it imports
        # extract_fees_on_demand locally inside the function).
        import v2.scraper.ondemand_vision as ov
        monkeypatch.setattr(ov, "extract_fees_on_demand", _spy_od)

        # Build CLI args and run via main().
        argv = ["--pdf-corpus-ondemand", "--mock-llm"]
        rc = pipeline_mod.main(argv)
        assert rc == 0

        # Spy got 2 PDFs × 4 asked_fields = 8 calls.
        assert len(calls) == 2 * 4, f"got {len(calls)} calls"

        # Distinct asked_fields are covered (the spec says tip+deposit+single+visa)
        asked = {c["asked_field"] for c in calls}
        assert asked == {"tip", "deposit", "single_supplement", "visa"}

        # Max pages cap is 3 (spec).
        assert all(c["max_vision_pages"] == 3 for c in calls)

        # Cache is shared across asked_fields per PDF (same cache instance reused).
        for c in calls:
            assert c["cache_provided"] is True

        # prior is provided (mirrors orchestrator runtime path).
        # First call per PDF has the regex-only prior; subsequent calls inherit
        # the merged result. Either way, prior is non-None.
        assert all(c["prior_provided"] for c in calls)

        # Two distinct pdf_hash values (one per PDF).
        assert len({c["pdf_hash"] for c in calls}) == 2


# ---- No live LLM call inside any of the above ------------------------------

class TestNoLiveLLMInPhase2Tests:
    """Defense-in-depth: every test in this module must run in mock mode."""

    def test_default_config_is_mock_mode(self, monkeypatch):
        # Clear any test-mode leak from sibling tests that set it (e.g.
        # the cassette-replay wiring test).
        monkeypatch.delenv("V2_STAGING_OPENAI_TEST_MODE", raising=False)
        monkeypatch.setenv("V2_STAGING_SUPABASE_URL", "http://x")
        monkeypatch.setenv("V2_STAGING_DB_HOST", "h")
        monkeypatch.setenv("V2_STAGING_DB_USER", "u")
        monkeypatch.setenv("V2_STAGING_DB_PASSWORD", "p")
        from v2.lib.config import load_config
        cfg = load_config(strict=True)
        assert cfg.openai_test_mode == "mock"
