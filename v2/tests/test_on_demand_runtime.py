"""
Sprint 4 follow-up wire-in tests — orchestrator triggers extract_fees_on_demand
when policy says we cannot answer the asked fee field from the existing DB row.

Spec test cases (per the assigned wire-in task):
  T1. Ask tip with missing/low-confidence row → invokes on-demand, then answers
       if extraction raises confidence ≥ 0.80.
  T2. Ask deposit — same behavior.
  T3. Ask single_supplement — answers only when on-demand lifts confidence ≥ 0.90;
       otherwise canned handoff (NO value leak).
  T4. High-confidence existing row does NOT call extract_fees_on_demand (cost saved).
  T5. pdf2image missing → graceful canned handoff, no crash.
  T6. Cache hit on repeat invocation avoids a second OpenAI/Vision call.
  T7. Updated tour_fees row persists per-field confidence values.

All tests run in mock mode (`MockLLMClient`); no live network, no real key.
"""

from __future__ import annotations

import io
import os
import shutil
from typing import Optional
from unittest.mock import patch

import pytest

from v2.lib.cache import _InMemoryRedis
from v2.lib.llm import MockLLMClient, LLMResponse, LLMUsage
from v2.lib.memory import MemoryService
from v2.lib.orchestrator import Orchestrator
from v2.scraper import ondemand_vision
from v2.scraper.extract_fees import ExtractionResult


# ---------------- helpers ----------------------------------------------------

def _build_synth_pdf(tmp_path) -> str:
    """English-keyword synthetic PDF (Thai font isn't available in reportlab default)."""
    from v2.tests.fixtures.synthetic_pdf import build_synthetic_fee_pdf
    body = (
        "Tour Fee Schedule:\n"
        "tip 1500 baht per pax\n"
        "deposit 10000 baht\n"
        "single supplement 5500 baht\n"
        "visa exempt for thai\n"
    )
    path = tmp_path / "synth.pdf"
    build_synthetic_fee_pdf(str(path), fee_text=body)
    return str(path)


def _dummy_pdf2image_returning_png():
    """Monkeypatch target for pdf2image.convert_from_path."""
    class _Img:
        def save(self, buf, format):
            buf.write(b"\x89PNG\r\n\x1a\n")
    def _conv(*args, **kwargs):
        return [_Img()]
    return _conv


class _StubLLM(MockLLMClient):
    """MockLLMClient with deterministic high-confidence vision responses."""
    def __init__(self, *, single_supp_conf: float = 0.92,
                 tip: int = 1500, deposit: int = 10000, single: int = 5500,
                 overall_conf: float = 0.92):
        super().__init__()
        self._vision_calls = 0
        self._single_supp_conf = single_supp_conf
        self._tip = tip
        self._deposit = deposit
        self._single = single
        self._overall_conf = overall_conf

    def vision(self, *, messages, image_bytes, max_tokens=None, response_format=None):
        self._vision_calls += 1
        self.call_log.append({"tier": "vision"})
        return LLMResponse(
            text="{}",
            structured={
                "tip_amount": self._tip,
                "deposit_amount": self._deposit,
                "single_supplement": self._single,
                "visa_status": "exempt",
                "extraction_confidence": self._overall_conf,
                "source_page": 4,
            },
            finish_reason="stop",
            usage=LLMUsage(tokens_in=120, tokens_out=80,
                             cost_usd_estimate=0.001,
                             model_used="gpt-4o", latency_ms=400),
        )


@pytest.fixture
def orch_locked(supabase, redis, make_customer, make_tour, tmp_path):
    """Orchestrator with a customer + locked tour ready for fee questions."""
    psid = "PSID_WIRE"
    cust_id = make_customer(psid, "Test")
    conv = supabase.table("conversations").insert({
        "customer_id": cust_id, "psid": psid,
        "state": "tour_selected",
    })
    pdf_path = _build_synth_pdf(tmp_path)
    tour = make_tour(
        web_code="ap_wire_001", tour_code_real="BCCKG27-HU",
        name="โตเกียว 5D4N", price=18999, airline="HU",
        country="ญี่ปุ่น", country_id=2,
    )
    # Patch tours_canonical to have pdf_url so on-demand can find it
    supabase.table("tours_canonical").update(
        {"id": tour["id"]}, {"pdf_url": "https://x.example.com/tour-wire.pdf"},
    )
    ms = MemoryService(supabase, redis)
    ms.lock_selected_tour(psid, supabase.table("tours_canonical").select_one({"id": tour["id"]}),
                          conversation_id=conv["id"])
    llm = _StubLLM()
    orch = Orchestrator(supabase, redis, llm)
    return {
        "orch": orch, "supabase": supabase, "redis": redis,
        "psid": psid, "tour_id": tour["id"], "llm": llm,
        "pdf_path": pdf_path, "conv_id": conv["id"],
    }


def _insert_fees(supabase, tour_id, *, pdf_hash="hashwire001" * 6, **fields):
    row = {
        "tour_id": tour_id,
        "tour_code_real": "BCCKG27-HU",
        "pdf_url": "https://x.example.com/tour-wire.pdf",
        "pdf_hash": pdf_hash[:64],
        "extraction_method": "pdfplumber+regex",
        "extraction_confidence": 0.60,
        "manually_verified": False,
    }
    row.update(fields)
    return supabase.table("tour_fees").insert(row)


# ---------------- T1: ask tip with low confidence → on-demand triggered ------

class TestTipLowConfidence:
    def test_low_conf_tip_triggers_ondemand_and_answers(self, orch_locked, tmp_path):
        ctx = orch_locked
        # Row exists but tip_confidence is below threshold (0.60 < 0.80).
        _insert_fees(ctx["supabase"], ctx["tour_id"],
                       tip_amount=1500, tip_confidence=0.60,
                       deposit_amount=10000, deposit_confidence=0.92)

        # Force vision-available and stub download_pdf to return the local synth PDF
        with patch.object(ondemand_vision, "vision_available", return_value=(True, None)), \
             patch("pdf2image.convert_from_path", side_effect=_dummy_pdf2image_returning_png()), \
             patch("v2.scraper.download_pdf.download_pdf") as dl_mock:
            from v2.scraper.download_pdf import PDFArtifact
            import time as _t
            dl_mock.return_value = PDFArtifact(
                url="https://x.example.com/tour-wire.pdf",
                local_path=ctx["pdf_path"],
                sha256="aa" * 32, size_bytes=1000,
                fetched_at=_t.time(), was_cached=False,
            )

            result = ctx["orch"].handle_turn(
                psid=ctx["psid"], text="ค่าทิปเท่าไหร่ครับ",
                meta_message_id="fb:wire_t1",
            )

        # On-demand was attempted
        tool_names = [t.get("tool") for t in result.tool_calls_made]
        assert "extract_fees_on_demand" in tool_names
        # Vision LLM was actually called
        assert ctx["llm"]._vision_calls >= 1
        # Bot answered from refreshed DB row
        assert result.reply_text is not None
        assert "1,500" in result.reply_text
        assert "ตามเอกสารโปรแกรม" in result.reply_text


# ---------------- T2: ask deposit with missing row → on-demand fills + answers

class TestDepositMissingRow:
    def test_missing_row_triggers_ondemand_and_answers_deposit(self, orch_locked):
        ctx = orch_locked
        # No tour_fees row at all → on-demand should be invoked
        with patch.object(ondemand_vision, "vision_available", return_value=(True, None)), \
             patch("pdf2image.convert_from_path", side_effect=_dummy_pdf2image_returning_png()), \
             patch("v2.scraper.download_pdf.download_pdf") as dl_mock:
            from v2.scraper.download_pdf import PDFArtifact
            import time as _t
            dl_mock.return_value = PDFArtifact(
                url="https://x.example.com/tour-wire.pdf",
                local_path=ctx["pdf_path"], sha256="bb" * 32,
                size_bytes=1000, fetched_at=_t.time(), was_cached=False,
            )

            result = ctx["orch"].handle_turn(
                psid=ctx["psid"], text="ต้องมัดจำเท่าไหร่ครับ",
                meta_message_id="fb:wire_t2",
            )

        tool_names = [t.get("tool") for t in result.tool_calls_made]
        assert "extract_fees_on_demand" in tool_names
        assert "10,000" in result.reply_text


# ---------------- T3: single_supplement gating at 0.90 ----------------------

class TestSingleSupplementGate:
    def test_lift_to_0_92_answers_single_supp(self, orch_locked):
        """Vision lifts single_supplement_confidence above 0.90 → answer."""
        ctx = orch_locked
        # Existing row has the value with regex baseline conf 0.60
        _insert_fees(ctx["supabase"], ctx["tour_id"],
                       single_supplement=5500, single_supplement_confidence=0.60,
                       tip_amount=1500, tip_confidence=0.92,
                       deposit_amount=10000, deposit_confidence=0.92)

        with patch.object(ondemand_vision, "vision_available", return_value=(True, None)), \
             patch("pdf2image.convert_from_path", side_effect=_dummy_pdf2image_returning_png()), \
             patch("v2.scraper.download_pdf.download_pdf") as dl_mock:
            from v2.scraper.download_pdf import PDFArtifact
            import time as _t
            dl_mock.return_value = PDFArtifact(
                url="https://x.example.com/tour-wire.pdf",
                local_path=ctx["pdf_path"], sha256="cc" * 32,
                size_bytes=1000, fetched_at=_t.time(), was_cached=False,
            )
            ctx["llm"]._overall_conf = 0.92  # vision raises baseline → 0.92

            result = ctx["orch"].handle_turn(
                psid=ctx["psid"], text="พักเดี่ยวเพิ่มเท่าไหร่",
                meta_message_id="fb:wire_t3a",
            )
        # Confirmed lifted → bot answers
        assert "5,500" in result.reply_text
        assert "ตามเอกสารโปรแกรม" in result.reply_text

    def test_lift_to_0_85_still_handsoff_single_supp(self, orch_locked):
        """Vision lifts to 0.85 — still below 0.90 strict threshold → handoff."""
        ctx = orch_locked
        _insert_fees(ctx["supabase"], ctx["tour_id"],
                       single_supplement=5500, single_supplement_confidence=0.60)

        with patch.object(ondemand_vision, "vision_available", return_value=(True, None)), \
             patch("pdf2image.convert_from_path", side_effect=_dummy_pdf2image_returning_png()), \
             patch("v2.scraper.download_pdf.download_pdf") as dl_mock:
            from v2.scraper.download_pdf import PDFArtifact
            import time as _t
            dl_mock.return_value = PDFArtifact(
                url="https://x.example.com/tour-wire.pdf",
                local_path=ctx["pdf_path"], sha256="dd" * 32,
                size_bytes=1000, fetched_at=_t.time(), was_cached=False,
            )
            ctx["llm"]._overall_conf = 0.85  # below strict 0.90

            result = ctx["orch"].handle_turn(
                psid=ctx["psid"], text="พักเดี่ยวเพิ่มเท่าไหร่",
                meta_message_id="fb:wire_t3b",
            )

        # Strict policy: do NOT leak the value
        assert "5,500" not in result.reply_text
        assert "5500" not in result.reply_text
        # Canned handoff message present
        assert ("ทีมงาน" in result.reply_text or "สักครู่" in result.reply_text)


# ---------------- T4: high-confidence row skips on-demand --------------------

class TestHighConfidenceSkipsOnDemand:
    def test_existing_high_conf_row_does_not_call_vision(self, orch_locked):
        ctx = orch_locked
        _insert_fees(ctx["supabase"], ctx["tour_id"],
                       tip_amount=1500, tip_confidence=0.95,
                       deposit_amount=10000, deposit_confidence=0.93,
                       single_supplement=5500, single_supplement_confidence=0.91,
                       visa_status="exempt", visa_confidence=0.90)

        # Even though we patch vision_available true, the orchestrator should
        # NOT invoke on-demand because the row already answers.
        with patch.object(ondemand_vision, "vision_available", return_value=(True, None)), \
             patch("pdf2image.convert_from_path", side_effect=_dummy_pdf2image_returning_png()), \
             patch("v2.scraper.download_pdf.download_pdf") as dl_mock:
            result = ctx["orch"].handle_turn(
                psid=ctx["psid"], text="ค่าทิปเท่าไหร่",
                meta_message_id="fb:wire_t4",
            )

            assert dl_mock.call_count == 0  # download was not attempted
        # No vision LLM call
        assert ctx["llm"]._vision_calls == 0
        # No on-demand tool entry in audit log
        tool_names = [t.get("tool") for t in result.tool_calls_made]
        assert "extract_fees_on_demand" not in tool_names
        # Bot answered from the existing row
        assert "1,500" in result.reply_text


# ---------------- T5: pdf2image missing → graceful handoff -------------------

class TestPDF2ImageMissingGraceful:
    def test_ocr_unavailable_returns_canned_handoff(self, orch_locked):
        ctx = orch_locked
        _insert_fees(ctx["supabase"], ctx["tour_id"],
                       tip_amount=None, tip_confidence=None,
                       deposit_amount=10000, deposit_confidence=0.92)

        with patch.object(ondemand_vision, "vision_available",
                          return_value=(False, "pdf2image_missing")), \
             patch("v2.scraper.download_pdf.download_pdf") as dl_mock:
            from v2.scraper.download_pdf import PDFArtifact
            import time as _t
            dl_mock.return_value = PDFArtifact(
                url="https://x.example.com/tour-wire.pdf",
                local_path=ctx["pdf_path"], sha256="ee" * 32,
                size_bytes=1000, fetched_at=_t.time(), was_cached=False,
            )

            result = ctx["orch"].handle_turn(
                psid=ctx["psid"], text="ค่าทิปเท่าไหร่",
                meta_message_id="fb:wire_t5",
            )

        # On-demand path was attempted (audit) but no vision call made
        tool_names = [t.get("tool") for t in result.tool_calls_made]
        assert "extract_fees_on_demand" in tool_names
        assert ctx["llm"]._vision_calls == 0
        # Bot responded with canned handoff (no value)
        assert result.reply_text is not None
        assert ("ทีมงาน" in result.reply_text or "สักครู่" in result.reply_text)


# ---------------- T6: cache hit avoids second vision call --------------------

class TestCacheHitAcrossTurns:
    def test_second_ask_same_pdf_uses_cache(self, orch_locked):
        ctx = orch_locked
        _insert_fees(ctx["supabase"], ctx["tour_id"],
                       tip_amount=1500, tip_confidence=0.60)

        with patch.object(ondemand_vision, "vision_available", return_value=(True, None)), \
             patch("pdf2image.convert_from_path", side_effect=_dummy_pdf2image_returning_png()), \
             patch("v2.scraper.download_pdf.download_pdf") as dl_mock:
            from v2.scraper.download_pdf import PDFArtifact
            import time as _t
            # Both turns target the same pdf_hash → cache hit on turn 2
            dl_mock.return_value = PDFArtifact(
                url="https://x.example.com/tour-wire.pdf",
                local_path=ctx["pdf_path"], sha256="ff" * 32,
                size_bytes=1000, fetched_at=_t.time(), was_cached=False,
            )

            ctx["orch"].handle_turn(
                psid=ctx["psid"], text="ค่าทิปเท่าไหร่",
                meta_message_id="fb:wire_t6a",
            )
            calls_after_first = ctx["llm"]._vision_calls
            assert calls_after_first >= 1

            ctx["orch"].handle_turn(
                psid=ctx["psid"], text="ค่าทิปเท่าไหร่อีกครั้ง",
                meta_message_id="fb:wire_t6b",
            )

        assert ctx["llm"]._vision_calls == calls_after_first


# ---------------- T7: persisted row carries per-field confidence -------------

class TestExtractionPersistsConfidence:
    def test_upsert_writes_per_field_confidence_columns(self, orch_locked):
        ctx = orch_locked
        _insert_fees(ctx["supabase"], ctx["tour_id"],
                       tip_amount=1500, tip_confidence=0.60,
                       deposit_amount=10000, deposit_confidence=0.92)

        with patch.object(ondemand_vision, "vision_available", return_value=(True, None)), \
             patch("pdf2image.convert_from_path", side_effect=_dummy_pdf2image_returning_png()), \
             patch("v2.scraper.download_pdf.download_pdf") as dl_mock:
            from v2.scraper.download_pdf import PDFArtifact
            import time as _t
            dl_mock.return_value = PDFArtifact(
                url="https://x.example.com/tour-wire.pdf",
                local_path=ctx["pdf_path"], sha256="aa" * 32,
                size_bytes=1000, fetched_at=_t.time(), was_cached=False,
            )

            ctx["orch"].handle_turn(
                psid=ctx["psid"], text="ค่าทิปเท่าไหร่",
                meta_message_id="fb:wire_t7",
            )

        row = ctx["supabase"].table("tour_fees").select_one({"tour_id": ctx["tour_id"]})
        assert row is not None
        # Vision lifted tip_confidence from 0.60 → ~0.92 (per stub)
        assert (row.get("tip_confidence") or 0) >= 0.85, f"got {row.get('tip_confidence')}"
        # extraction_version backfilled
        assert row.get("extraction_version") == "1.0"


# ---------------- N1 unit test: vision lift overrides regex baseline --------

class TestN1VisionLiftOverridesRegex:
    """Direct unit test for the bug N1 fixed in this patch."""

    def test_vision_lift_overrides_lower_regex_confidence(self):
        from v2.scraper.extract_fees import ExtractionResult
        from v2.scraper.ondemand_vision import _bump_field_confidence_from_vision

        merged = ExtractionResult(
            single_supplement=5500, single_supplement_confidence=0.60,
            tip_amount=1500, tip_confidence=0.70,
        )
        page = ExtractionResult(
            single_supplement=5500, tip_amount=1500,
            extraction_confidence=0.92,
        )
        _bump_field_confidence_from_vision(merged, page)
        # Both fields lifted; pre-fix, neither was changed.
        assert merged.single_supplement_confidence == 0.92
        assert merged.tip_confidence == 0.92

    def test_vision_lower_confidence_does_not_downgrade(self):
        from v2.scraper.extract_fees import ExtractionResult
        from v2.scraper.ondemand_vision import _bump_field_confidence_from_vision

        merged = ExtractionResult(
            single_supplement=5500, single_supplement_confidence=0.92,
        )
        page = ExtractionResult(
            single_supplement=5500, extraction_confidence=0.70,
        )
        _bump_field_confidence_from_vision(merged, page)
        assert merged.single_supplement_confidence == 0.92  # unchanged
