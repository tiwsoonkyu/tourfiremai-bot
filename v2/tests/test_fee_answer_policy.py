"""
Sprint 4 follow-up tests — fee_answer_policy + selected_tour-first answers.

Spec test cases (per assigned task):
  1. selected tour + ask tip                          → answer from fee DB
  2. selected tour + ask deposit                      → answer from fee DB
  3. selected tour + ask single supplement low conf   → handoff
  4. missing fee → on-demand extraction triggered     → see test_ondemand_vision.py
  5. repeated same PDF → cache hit, no 2nd OpenAI     → see test_ondemand_vision.py
  6. OCR unavailable → graceful handoff, no crash     → see test_ondemand_vision.py
"""

import pytest

from v2.lib.fee_answer_policy import (
    decide_fee_answer, detect_asked_field, format_fee_answer,
    DEFAULT_THRESHOLD, SINGLE_SUPPLEMENT_THRESHOLD,
)


# --- detect_asked_field ------------------------------------------------------

class TestDetectAskedField:
    def test_tip_thai(self):
        assert detect_asked_field("ค่าทิปเท่าไหร่") == "tip"

    def test_tip_english(self):
        assert detect_asked_field("how much is the tip?") == "tip"

    def test_deposit_thai(self):
        assert detect_asked_field("ต้องมัดจำเท่าไหร่ครับ") == "deposit"

    def test_single_supplement(self):
        assert detect_asked_field("พักเดี่ยวเพิ่มกี่บาท") == "single_supplement"

    def test_visa(self):
        assert detect_asked_field("วีซ่าต้องทำไหม") == "visa"

    def test_no_bed_priority_over_child(self):
        # "ไม่เสริมเตียง" should win over generic child match
        assert detect_asked_field("เด็กไม่เสริมเตียงราคา") == "child_no_bed"

    def test_generic_returns_any(self):
        assert detect_asked_field("ราคารวมเท่าไหร่") == "any"

    def test_empty_returns_any(self):
        assert detect_asked_field("") == "any"
        assert detect_asked_field(None) == "any"


# --- decide_fee_answer -------------------------------------------------------

class TestDecideFeeAnswer:
    def test_no_row_returns_handoff(self):
        d = decide_fee_answer(None, "tip")
        assert d.decision == "handoff_no_fees_row"
        assert d.can_answer is False

    def test_tip_with_high_per_field_confidence_returns_answer(self):
        row = {"tip_amount": 1500, "tip_confidence": 0.95}
        d = decide_fee_answer(row, "tip")
        assert d.decision == "answer"
        assert d.value == 1500
        assert d.confidence == 0.95
        assert d.threshold == DEFAULT_THRESHOLD

    def test_deposit_with_high_per_field_confidence_returns_answer(self):
        row = {"deposit_amount": 10000, "deposit_confidence": 0.90}
        d = decide_fee_answer(row, "deposit")
        assert d.decision == "answer"
        assert d.value == 10000

    def test_single_supplement_below_strict_threshold_handsoff(self):
        # 0.80 would pass for tip; for single_supplement requires 0.90
        row = {"single_supplement": 5500, "single_supplement_confidence": 0.80}
        d = decide_fee_answer(row, "single_supplement")
        assert d.decision == "handoff_low_confidence"
        assert d.threshold == SINGLE_SUPPLEMENT_THRESHOLD
        assert d.can_answer is False
        # value is reported in the decision but the response writer must NOT echo it
        assert d.value == 5500
        assert d.handoff_reason == "below_threshold"

    def test_single_supplement_at_or_above_strict_threshold_answers(self):
        row = {"single_supplement": 5500, "single_supplement_confidence": 0.91}
        d = decide_fee_answer(row, "single_supplement")
        assert d.decision == "answer"

    def test_per_field_null_falls_back_to_row_level(self):
        # Pre-migration-019 rows: per-field column is NULL
        row = {"tip_amount": 1500, "tip_confidence": None,
               "extraction_confidence": 0.85}
        d = decide_fee_answer(row, "tip")
        assert d.decision == "answer"
        assert d.confidence == 0.85

    def test_visa_status_exempt_with_high_conf_answers(self):
        row = {"visa_fee": None, "visa_status": "exempt", "visa_confidence": 0.95}
        d = decide_fee_answer(row, "visa")
        assert d.decision == "answer"
        assert d.value == 0
        assert d.visa_status == "exempt"

    def test_visa_status_exempt_with_low_conf_handsoff(self):
        row = {"visa_fee": None, "visa_status": "exempt", "visa_confidence": 0.50}
        d = decide_fee_answer(row, "visa")
        assert d.decision == "handoff_low_confidence"

    def test_field_value_missing_handsoff(self):
        row = {"tip_amount": None, "tip_confidence": 0.99}
        d = decide_fee_answer(row, "tip")
        assert d.decision == "handoff_missing"

    def test_generic_any_requires_full_row(self):
        # missing single_supplement → cannot answer "any"
        row = {"tip_amount": 1500, "deposit_amount": 10000,
               "single_supplement": None, "extraction_confidence": 0.85,
               "visa_status": "exempt"}
        d = decide_fee_answer(row, "any")
        assert d.decision == "handoff_low_confidence"

    def test_generic_any_with_full_row_answers(self):
        row = {"tip_amount": 1500, "deposit_amount": 10000,
               "single_supplement": 5500, "extraction_confidence": 0.86,
               "visa_status": "exempt"}
        d = decide_fee_answer(row, "any")
        assert d.decision == "answer"


# --- format_fee_answer -------------------------------------------------------

class TestFormatFeeAnswer:
    def test_tip_formatting(self):
        row = {"tip_amount": 1500, "tip_confidence": 0.95}
        d = decide_fee_answer(row, "tip")
        out = format_fee_answer(d)
        assert "ค่าทิป" in out
        assert "1,500" in out
        assert "ตามเอกสารโปรแกรม" in out

    def test_deposit_formatting(self):
        row = {"deposit_amount": 10000, "deposit_confidence": 0.90}
        d = decide_fee_answer(row, "deposit")
        out = format_fee_answer(d)
        assert "ค่ามัดจำ" in out
        assert "10,000" in out

    def test_visa_exempt_formatting(self):
        row = {"visa_status": "exempt", "visa_fee": None, "visa_confidence": 0.95}
        d = decide_fee_answer(row, "visa")
        out = format_fee_answer(d)
        assert "ฟรี" in out or "ไม่ต้องใช้วีซ่า" in out
        assert "ตามเอกสารโปรแกรม" in out

    def test_format_raises_on_non_answer(self):
        row = {"tip_amount": None}
        d = decide_fee_answer(row, "tip")
        with pytest.raises(ValueError):
            format_fee_answer(d)


# --- selected_tour-first response writer integration ------------------------

class TestSelectedTourFirstFeeAnswers:
    """
    Integration: orchestrator + response_writer path for FEE_CHECK_REQUIRED.

    Tests the three policy outcomes via the orchestrator entry point and
    asserts NO response-tier LLM call is made (canned answers / handoff only).
    """

    @pytest.fixture
    def orch_with_locked_tour(self, supabase, redis, make_customer, make_tour):
        from v2.lib.orchestrator import Orchestrator
        from v2.lib.llm import MockLLMClient
        from v2.lib.memory import MemoryService

        psid = "PSID_FEE_S4"
        cust_id = make_customer(psid, "Test")
        conv = supabase.table("conversations").insert({
            "customer_id": cust_id, "psid": psid,
            "state": "tour_selected",
        })
        tour = make_tour(
            web_code="ap_s4_001", tour_code_real="BCCKG27-HU",
            name="โตเกียว 5D4N", price=18999, airline="HU",
            country="ญี่ปุ่น", country_id=2,
        )
        # Lock the tour via MemoryService so selected_tours row exists
        ms = MemoryService(supabase, redis)
        ms.lock_selected_tour(psid, tour, conversation_id=conv["id"])

        spy_llm = MockLLMClient()
        orch = Orchestrator(supabase, redis, spy_llm)
        return {
            "orch": orch, "supabase": supabase, "psid": psid,
            "tour_id": tour["id"], "llm": spy_llm, "conv_id": conv["id"],
        }

    def _insert_fees(self, supabase, tour_id, **fields):
        row = {
            "tour_id": tour_id,
            "tour_code_real": "BCCKG27-HU",
            "pdf_url": "https://x.example.com/tour.pdf",
            "pdf_hash": "deadbeef" * 8,  # 64 hex
            "extraction_method": "pdfplumber+regex",
            "extraction_confidence": 0.85,
            "manually_verified": False,
        }
        row.update(fields)
        return supabase.table("tour_fees").insert(row)

    # ---- Test 1: ask tip → answer from DB --------------------------------

    def test_ask_tip_with_high_confidence_answers_from_db(self, orch_with_locked_tour):
        ctx = orch_with_locked_tour
        self._insert_fees(ctx["supabase"], ctx["tour_id"],
                          tip_amount=1500, tip_confidence=0.95,
                          deposit_amount=10000, deposit_confidence=0.92,
                          single_supplement=5500, single_supplement_confidence=0.55,
                          visa_status="exempt", visa_confidence=0.90)

        result = ctx["orch"].handle_turn(
            psid=ctx["psid"], text="ค่าทิปเท่าไหร่ครับ",
            meta_message_id="fb:s4_tip_1",
        )
        assert result.reply_text is not None
        assert "1,500" in result.reply_text
        assert "ตามเอกสารโปรแกรม" in result.reply_text
        # NO response-tier LLM call was made (it was the policy canned answer)
        response_calls = [c for c in ctx["llm"].call_log if c.get("tier") == "response"]
        assert len(response_calls) == 0
        assert result.decision in ("canned_fee_answer", "canned_handoff")

    # ---- Test 2: ask deposit → answer from DB ----------------------------

    def test_ask_deposit_with_high_confidence_answers_from_db(self, orch_with_locked_tour):
        ctx = orch_with_locked_tour
        self._insert_fees(ctx["supabase"], ctx["tour_id"],
                          tip_amount=1500, tip_confidence=0.92,
                          deposit_amount=10000, deposit_confidence=0.93,
                          single_supplement=5500, single_supplement_confidence=0.55,
                          visa_status="exempt", visa_confidence=0.90)

        result = ctx["orch"].handle_turn(
            psid=ctx["psid"], text="มัดจำเท่าไหร่",
            meta_message_id="fb:s4_dep_1",
        )
        assert result.reply_text is not None
        assert "10,000" in result.reply_text
        assert "ตามเอกสารโปรแกรม" in result.reply_text
        response_calls = [c for c in ctx["llm"].call_log if c.get("tier") == "response"]
        assert len(response_calls) == 0

    # ---- Test 3: ask single supplement with low confidence → handoff ------

    def test_ask_single_supplement_low_confidence_handsoff(self, orch_with_locked_tour):
        ctx = orch_with_locked_tour
        self._insert_fees(ctx["supabase"], ctx["tour_id"],
                          tip_amount=1500, tip_confidence=0.95,
                          deposit_amount=10000, deposit_confidence=0.93,
                          single_supplement=5500, single_supplement_confidence=0.60,
                          visa_status="exempt", visa_confidence=0.90)

        result = ctx["orch"].handle_turn(
            psid=ctx["psid"], text="พักเดี่ยวเพิ่มเท่าไหร่",
            meta_message_id="fb:s4_ss_1",
        )
        assert result.reply_text is not None
        # Must NOT leak the 5500 value (confidence below 0.90)
        assert "5,500" not in result.reply_text
        assert "5500" not in result.reply_text
        # Must be canned handoff text
        assert ("ทีมงาน" in result.reply_text or "สักครู่" in result.reply_text)
        # NO LLM response-tier call
        response_calls = [c for c in ctx["llm"].call_log if c.get("tier") == "response"]
        assert len(response_calls) == 0
