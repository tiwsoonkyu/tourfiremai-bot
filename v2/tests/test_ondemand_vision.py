"""
Sprint 4 follow-up tests — on-demand vision extraction + cache + OCR-unavailable.

Tests 4, 5, 6 from the spec:
  4. missing fee + asked field → on-demand extraction is invoked, fills the gap
  5. repeated same PDF → cache hit, no second OpenAI call
  6. OCR unavailable → graceful handoff, no crash
"""

import json
import os
import sys
from unittest.mock import patch

import pytest

from v2.lib.cache import _InMemoryRedis
from v2.lib.llm import MockLLMClient, LLMResponse, LLMUsage
from v2.scraper import ondemand_vision
from v2.scraper.extract_fees import ExtractionResult


# Use the synthetic PDF helper that already exists in fixtures (Sprint 3).
@pytest.fixture
def synth_pdf(tmp_path):
    """Synthetic PDF using English fee keywords so pdf_classifier's regex matches."""
    from v2.tests.fixtures.synthetic_pdf import build_synthetic_fee_pdf
    body = (
        "Tour Fee Schedule (alphabetical):\n"
        "tip 1500 baht per pax\n"
        "deposit 10000 baht\n"
        "single supplement 5500 baht\n"
        "visa exempt for thai passport holders\n"
        "infant 4500 baht\n"
    )
    path = tmp_path / "synth.pdf"
    build_synthetic_fee_pdf(str(path), fee_text=body)
    return str(path)


def _stub_vision_response(*, tip=None, deposit=None, single=None, visa_status=None,
                          confidence=0.90, source_page=4):
    return LLMResponse(
        text="{}", structured={
            "tip_amount": tip, "deposit_amount": deposit,
            "single_supplement": single, "visa_status": visa_status,
            "extraction_confidence": confidence, "source_page": source_page,
            "notes": "vision_stub",
        },
        finish_reason="stop",
        usage=LLMUsage(tokens_in=120, tokens_out=80,
                       cost_usd_estimate=0.001, model_used="gpt-4o", latency_ms=400),
    )


class _StubLLM(MockLLMClient):
    """MockLLMClient with deterministic vision responses for the test corpus."""
    def __init__(self):
        super().__init__()
        self._vision_calls = 0

    def vision(self, *, messages, image_bytes, max_tokens=None, response_format=None):
        self._vision_calls += 1
        self.call_log.append({"tier": "vision", "messages_summary": "...vision..."})
        # Return a high-confidence single_supplement to demonstrate the lift
        return _stub_vision_response(
            tip=1500, deposit=10000, single=5500, visa_status="exempt",
            confidence=0.92,
        )


# ---------------- Test 4: missing fee → on-demand triggered -----------------

class TestOnDemandTriggered:
    def test_no_prior_invokes_vision_and_fills_fields(self, synth_pdf):
        cache = _InMemoryRedis()
        llm = _StubLLM()

        # Force "vision available" since the sandbox may not have pdf2image installed
        with patch.object(ondemand_vision, "vision_available", return_value=(True, None)), \
             patch.object(ondemand_vision, "pdf2image_render_first_page", create=True), \
             patch("pdf2image.convert_from_path", create=True) as conv:
            # Make pdf2image.convert_from_path return a dummy PIL-image-shape that can .save()
            class _DummyImg:
                def save(self, buf, format): buf.write(b"\x89PNG\r\n\x1a\n")
            conv.return_value = [_DummyImg()]

            od = ondemand_vision.extract_fees_on_demand(
                synth_pdf, llm,
                pdf_hash="hash_test4_aaaaaa",
                cache=cache,
                max_vision_pages=3,
                asked_field="single_supplement",
            )

        assert od.cache_hit is False
        assert od.ocr_available is True
        assert od.skipped_reason is None
        # Vision was called at least once
        assert llm._vision_calls >= 1
        # Fields were filled
        r = od.result
        assert r.tip_amount == 1500
        assert r.single_supplement == 5500
        # Per-field confidences were bumped — but capped at
        # VISION_PER_FIELD_CAP=0.84 because no regex baseline corroborates
        # (added in Phase 2 live-accuracy follow-up). Vision-only single_supp
        # stays below policy threshold 0.90, which is the safety design.
        from v2.scraper.ondemand_vision import VISION_PER_FIELD_CAP
        assert r.single_supplement_confidence is not None
        assert r.single_supplement_confidence == VISION_PER_FIELD_CAP

    def test_respects_max_vision_pages_cap(self, synth_pdf):
        cache = _InMemoryRedis()
        llm = _StubLLM()
        with patch.object(ondemand_vision, "vision_available", return_value=(True, None)), \
             patch("pdf2image.convert_from_path", create=True) as conv:
            class _DummyImg:
                def save(self, buf, format): buf.write(b"\x89PNG\r\n\x1a\n")
            conv.return_value = [_DummyImg()]

            # Pretend classifier returned many fee pages → still capped
            from v2.scraper import ondemand_vision as ov
            with patch.object(ov, "select_candidate_pages",
                              return_value=[1, 2, 3, 4, 5, 6, 7]):
                od = ov.extract_fees_on_demand(
                    synth_pdf, llm,
                    pdf_hash="hash_test4_cap",
                    cache=cache, max_vision_pages=3,
                )
        assert od.vision_pages_used <= 3


# ---------------- Test 5: repeated PDF → cache hit -------------------------

class TestCacheHit:
    def test_second_call_same_hash_skips_openai(self, synth_pdf):
        cache = _InMemoryRedis()
        llm = _StubLLM()

        with patch.object(ondemand_vision, "vision_available", return_value=(True, None)), \
             patch("pdf2image.convert_from_path", create=True) as conv:
            class _DummyImg:
                def save(self, buf, format): buf.write(b"\x89PNG\r\n\x1a\n")
            conv.return_value = [_DummyImg()]

            od1 = ondemand_vision.extract_fees_on_demand(
                synth_pdf, llm,
                pdf_hash="cache_hash_001",
                cache=cache, max_vision_pages=2,
            )
            calls_after_first = llm._vision_calls
            assert od1.cache_hit is False
            assert calls_after_first >= 1

            od2 = ondemand_vision.extract_fees_on_demand(
                synth_pdf, llm,
                pdf_hash="cache_hash_001",  # SAME hash → cache HIT
                cache=cache, max_vision_pages=2,
            )

        assert od2.cache_hit is True
        # No additional LLM vision calls on the second run
        assert llm._vision_calls == calls_after_first
        # Cached result preserves fields
        assert od2.result.tip_amount == od1.result.tip_amount

    def test_different_extraction_version_invalidates_cache(self, synth_pdf):
        cache = _InMemoryRedis()
        llm = _StubLLM()

        with patch.object(ondemand_vision, "vision_available", return_value=(True, None)), \
             patch("pdf2image.convert_from_path", create=True) as conv:
            class _DummyImg:
                def save(self, buf, format): buf.write(b"\x89PNG\r\n\x1a\n")
            conv.return_value = [_DummyImg()]

            ondemand_vision.extract_fees_on_demand(
                synth_pdf, llm, pdf_hash="hash_ver_1",
                cache=cache, max_vision_pages=1,
                extraction_version="1.0",
            )
            calls_after_first = llm._vision_calls

            ondemand_vision.extract_fees_on_demand(
                synth_pdf, llm, pdf_hash="hash_ver_1",
                cache=cache, max_vision_pages=1,
                extraction_version="2.0",  # different version → cache MISS
            )
        assert llm._vision_calls > calls_after_first


# ---------------- Test 6: OCR unavailable → graceful handoff ---------------

class TestOCRUnavailable:
    def test_missing_pdf2image_returns_graceful_skip(self, synth_pdf):
        cache = _InMemoryRedis()
        llm = _StubLLM()

        with patch.object(ondemand_vision, "vision_available",
                          return_value=(False, "pdf2image_missing")):
            od = ondemand_vision.extract_fees_on_demand(
                synth_pdf, llm,
                pdf_hash="hash_ocr_missing",
                cache=cache, max_vision_pages=3,
                asked_field="single_supplement",
            )

        # No crash. OCR-unavailable surfaced.
        assert od.ocr_available is False
        assert od.skipped_reason == "pdf2image_missing"
        # Cache NOT populated (env may become available later)
        assert od.cache_hit is False
        # The returned ExtractionResult is the prior (or empty), not synthesized
        assert "ocr_unavailable" in (od.result.extraction_errors[0] if od.result.extraction_errors else "")
        # No vision LLM calls made
        assert llm._vision_calls == 0

    def test_missing_pdf2image_with_prior_returns_prior(self, synth_pdf):
        cache = _InMemoryRedis()
        llm = _StubLLM()
        prior = ExtractionResult(
            tip_amount=1500, tip_confidence=0.70,
            extraction_method="pdfplumber+regex",
            extraction_confidence=0.70,
        )
        with patch.object(ondemand_vision, "vision_available",
                          return_value=(False, "pdf2image_missing")):
            od = ondemand_vision.extract_fees_on_demand(
                synth_pdf, llm,
                pdf_hash="hash_ocr_missing_with_prior",
                prior=prior, cache=cache, max_vision_pages=3,
            )
        # Prior survives unchanged (graceful)
        assert od.result.tip_amount == 1500
        assert od.result.tip_confidence == 0.70


# ---------------- Candidate page selection sanity --------------------------

class TestSelectCandidatePages:
    def test_prefers_fee_keyword_pages_then_scanned(self):
        from v2.lib.pdf_classifier import PdfClassification
        cls = PdfClassification(
            kind="mixed", total_pages=10, text_pages=8,
            scanned_pages=[5, 9],
            fee_pages=[2, 4, 7],
            pages=[],
        )
        picked = ondemand_vision.select_candidate_pages(cls, max_pages=3)
        assert picked == [2, 4, 7]

    def test_falls_through_to_scanned_when_short_on_fee_pages(self):
        from v2.lib.pdf_classifier import PdfClassification
        cls = PdfClassification(
            kind="mixed", total_pages=10, text_pages=8,
            scanned_pages=[5, 9],
            fee_pages=[4],
            pages=[],
        )
        picked = ondemand_vision.select_candidate_pages(cls, max_pages=3)
        assert picked == [4, 5, 9]

    def test_max_pages_zero_returns_empty(self):
        from v2.lib.pdf_classifier import PdfClassification
        cls = PdfClassification(
            kind="text", total_pages=5, text_pages=5,
            scanned_pages=[], fee_pages=[1, 2, 3], pages=[],
        )
        assert ondemand_vision.select_candidate_pages(cls, max_pages=0) == []
