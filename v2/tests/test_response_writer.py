"""Sprint 3 test: response_writer guard rails + state-silence."""

import pytest
from v2.lib.response_writer import (
    write_response, _strip_wholesale, _has_brand_leak, _truncate,
    CANNED_HANDOFF_FEE_INCOMPLETE, CANNED_HANDOFF_GENERIC,
)
from v2.lib.state_machine import State
from v2.lib.llm import MockLLMClient


# --- Helpers ------------------------------------------------------------------

class _DummyLLM(MockLLMClient):
    """MockLLMClient subclass that lets tests override the next reply."""
    def __init__(self, next_text: str = "(default mock)"):
        super().__init__()
        self.next_text = next_text
        self.last_user_text = ""
    def chat(self, **kw):
        messages = kw.get("messages", [])
        user_msg = next((m for m in messages if m.get("role") == "user"), {})
        self.last_user_text = str(user_msg.get("content", ""))
        rsp = super().chat(**kw)
        rsp.text = self.next_text
        return rsp


class _FailingLLM(MockLLMClient):
    """LLM that raises so fallback behavior is explicit in tests."""
    def chat(self, **kw):
        raise RuntimeError("boom")


# --- Utility tests ------------------------------------------------------------

class TestStripWholesale:
    def test_drops_top_level(self):
        out = _strip_wholesale({"name": "X", "wholesale": "GS", "price": 100})
        assert "wholesale" not in out
        assert out["name"] == "X"

    def test_drops_nested(self):
        out = _strip_wholesale({"tour": {"name": "X", "wholesale": "TTN"}})
        assert "wholesale" not in out["tour"]

    def test_case_insensitive(self):
        out = _strip_wholesale({"Wholesale": "GS", "WHOLESALE": "TTN"})
        assert out == {}

    def test_handles_lists(self):
        out = _strip_wholesale({"tours": [{"name": "A", "wholesale": "GS"},
                                            {"name": "B", "wholesale": "TTN"}]})
        for t in out["tours"]:
            assert "wholesale" not in t


class TestBrandLeak:
    @pytest.mark.parametrize("text", [
        "ลูกค้ารับทราบจาก TTN",
        "Best Tour ทัวร์น่าสนใจ",
        "GS Travel partner",
        "Zego เพิ่งออกโปร",
        "ส่งจาก formosa",
    ])
    def test_detects(self, text):
        assert _has_brand_leak(text) is True

    @pytest.mark.parametrize("text", [
        "สวัสดีค่ะ ทัวร์โตเกียวน่าสนใจค่ะ",
        "ราคา 25,900 บาท",
        "ค่าทิป 1,500 บาท",
    ])
    def test_clean(self, text):
        assert _has_brand_leak(text) is False





class TestBrandLeakFalsePositives:
    """QA flagged: previous substring blacklist matched innocent text like
    'tags', 'the best in Tokyo', 'check in 14.00 น.'. Word-boundary regex fixes this."""

    @pytest.mark.parametrize("text", [
        "เช็คอินโรงแรม 14.00 น.",
        "เริ่ม check in เวลา 15.00",
        "the best of Tokyo",
        "ทัวร์ดีที่สุดในญี่ปุ่น",
        "tags ที่แนะนำสำหรับนักท่องเที่ยว",
        "พระราชวังต้องห้าม",
        "messages ที่ลูกค้าส่งมา",
        "เลือกที่นั่ง GSM ได้",
    ])
    def test_no_false_positive(self, text):
        assert _has_brand_leak(text) is False, f"False positive on: {text!r}"

    @pytest.mark.parametrize("text", [
        "TTN เกิดมาเที่ยว",
        "ทัวร์นี้ของ TTN",
        "Best Tour Group",
        "Zego เพิ่งออกโปร",
        "ส่งจาก Formosa",
        "GS Travel",
        "GS Tour",
        "I Travel agency",
        "I-Travel ตัวแทน",
        "Rich Tour ผู้จัด",
    ])
    def test_actual_brand_caught(self, text):
        assert _has_brand_leak(text) is True, f"Missed brand in: {text!r}"


class TestTruncate:
    def test_under_limit(self):
        assert _truncate("hello", 100) == "hello"

    def test_over_limit(self):
        out = _truncate("x" * 500, 100)
        assert len(out) == 100
        assert out.endswith("...")


# --- State-silence tests ------------------------------------------------------

class TestStateSilence:
    def test_human_paused_returns_silent(self):
        llm = _DummyLLM()
        rd = write_response(state=State.HUMAN_PAUSED, intent_type="greeting",
                              tool_results={}, customer_memory={}, llm=llm)
        assert rd.text is None
        assert rd.decision == "silent"
        # LLM must NOT be called
        assert llm.call_log == []

    def test_closed_returns_silent(self):
        llm = _DummyLLM()
        rd = write_response(state=State.CLOSED, intent_type="greeting",
                              tool_results={}, customer_memory={}, llm=llm)
        assert rd.text is None
        assert llm.call_log == []


# --- Canned-path tests --------------------------------------------------------

class TestCannedPaths:
    def test_fee_incomplete_returns_canned_no_llm(self):
        llm = _DummyLLM()
        rd = write_response(
            state=State.FEE_CHECK_REQUIRED, intent_type="ask_fee",
            tool_results={"fees": {"is_complete": False}},
            customer_memory={}, llm=llm,
        )
        assert rd.text == CANNED_HANDOFF_FEE_INCOMPLETE
        assert rd.used_canned is True
        assert rd.used_llm is False
        assert llm.call_log == []

    def test_booking_ready_returns_canned(self):
        llm = _DummyLLM()
        rd = write_response(
            state=State.BOOKING_READY_FOR_HANDOFF, intent_type="confirm_booking",
            tool_results={}, customer_memory={}, llm=llm,
        )
        assert rd.text == CANNED_HANDOFF_GENERIC
        assert llm.call_log == []

    def test_waiting_team_returns_canned(self):
        llm = _DummyLLM()
        rd = write_response(state=State.WAITING_TEAM, intent_type="greeting",
                              tool_results={}, customer_memory={}, llm=llm)
        assert "สักครู่" in rd.text or "ทีมงาน" in rd.text
        assert llm.call_log == []

    def test_search_tours_returns_deterministic_reply_no_llm(self):
        llm = _DummyLLM()
        rd = write_response(
            state=State.OPTIONS_PRESENTED,
            intent_type="ask_country",
            tool_results={
                "search_tours": {
                    "tours": [
                        {
                            "rank": 1,
                            "web_code": "ap111",
                            "tour_code_real": "JP-REAL-1",
                            "name": "Tokyo Value 5D",
                            "price": 18999,
                            "days": 5,
                            "airline": "XJ",
                            "url": "https://www.tourfiremai.com/tour/ap111",
                        },
                        {
                            "rank": 2,
                            "web_code": "ap222",
                            "tour_code_real": "JP-REAL-2",
                            "name": "Osaka 5D",
                            "price": 25900,
                            "days": 5,
                            "airline": "VZ",
                            "url": "https://www.tourfiremai.com/tour/ap222",
                        },
                    ],
                    "query_echo": {"country_id": 2},
                }
            },
            customer_memory={},
            llm=llm,
        )
        assert rd.used_canned is True
        assert rd.used_llm is False
        assert rd.decision == "canned_search_results"
        assert llm.call_log == []
        assert "ap111" in rd.text
        assert "JP-REAL-1" in rd.text
        assert "18,999" in rd.text
        assert "สนใจตัวไหน" in rd.text


    def test_search_tours_filters_fixture_rows_before_rendering(self):
        llm = _DummyLLM()
        rd = write_response(
            state=State.OPTIONS_PRESENTED,
            intent_type="ask_country",
            tool_results={
                "search_tours": {
                    "tours": [
                        {
                            "rank": 1,
                            "web_code": "ap_itest_lock_a3649c",
                            "tour_code_real": None,
                            "name": "T",
                            "price": 1000,
                            "days": 5,
                            "airline": None,
                            "url": "https://x",
                        },
                        {
                            "rank": 2,
                            "web_code": "ap333",
                            "tour_code_real": "JP-REAL-3",
                            "name": "Tokyo Real 5D",
                            "price": 19999,
                            "days": 5,
                            "airline": "XJ",
                            "url": "https://www.tourfiremai.com/tour/ap333",
                        },
                    ],
                    "query_echo": {"country_id": 2},
                }
            },
            customer_memory={},
            llm=llm,
        )
        assert rd.used_canned is True
        assert rd.used_llm is False
        assert llm.call_log == []
        assert "ap_itest" not in rd.text
        assert "https://x" not in rd.text
        assert "1,000" not in rd.text
        assert "ap333" in rd.text
        assert "JP-REAL-3" in rd.text

    def test_search_tours_empty_known_country_uses_llm_with_safe_status(self):
        llm = _DummyLLM("smart safe reply")
        rd = write_response(
            state=State.OPTIONS_PRESENTED,
            intent_type="ask_country",
            tool_results={
                "search_tours": {
                    "tours": [],
                    "query_echo": {"country_id": 2},
                }
            },
            customer_memory={},
            llm=llm,
        )
        assert rd.used_canned is False
        assert rd.used_llm is True
        assert rd.decision == "llm_reply"
        assert rd.text == "smart safe reply"
        assert len(llm.call_log) == 1
        sent = llm.last_user_text
        assert "safe_search_status" in sent
        assert "no_customer_visible_tours" in sent
        assert rd.text != CANNED_HANDOFF_GENERIC
        assert "15" not in rd.text

    def test_search_tours_empty_known_inputs_tells_llm_not_to_repeat_known_fields(self):
        llm = _DummyLLM("smart safe reply")
        rd = write_response(
            state=State.OPTIONS_PRESENTED,
            intent_type="ask_period",
            tool_results={
                "search_tours": {
                    "tours": [],
                    "query_echo": {
                        "country": "ญี่ปุ่น",
                        "country_id": 2,
                        "budget": 30000,
                        "travel_period": "เดือนหน้า",
                    },
                }
            },
            customer_memory={
                "latest_country": "ญี่ปุ่น",
                "budget_per_person": 30000,
                "travel_month": "เดือนหน้า",
            },
            llm=llm,
        )
        assert rd.used_llm is True
        sent = llm.last_user_text
        assert "safe_search_status" in sent
        assert "known_inputs" in sent
        assert "budget_per_person" in sent
        assert "30,000" in sent
        assert "travel_period" in sent
        assert "Do not ask again" in sent
        assert "missing_inputs" in sent

    def test_search_tours_all_fixture_rows_hidden_from_llm_and_llm_used(self):
        llm = _DummyLLM("smart safe reply")
        rd = write_response(
            state=State.OPTIONS_PRESENTED,
            intent_type="ask_country",
            tool_results={
                "search_tours": {
                    "tours": [
                        {
                            "rank": 1,
                            "web_code": "ap_itest_lock_a3649c",
                            "tour_code_real": None,
                            "name": "T",
                            "price": 1000,
                            "days": 5,
                            "airline": None,
                            "url": "https://x",
                        }
                    ],
                    "query_echo": {"country_id": 2},
                }
            },
            customer_memory={},
            llm=llm,
        )
        assert rd.used_llm is True
        assert rd.decision == "llm_reply"
        assert rd.text == "smart safe reply"
        sent = llm.last_user_text
        assert "safe_search_status" in sent
        assert "unsafe_rows_dropped" in sent
        assert "ap_itest" not in sent
        assert "https://x" not in sent
        assert "1,000" not in sent
        assert "ap_itest" not in rd.text
        assert "https://x" not in rd.text

    def test_llm_error_for_empty_known_country_returns_low_risk_fallback_not_handoff(self):
        llm = _FailingLLM()
        rd = write_response(
            state=State.OPTIONS_PRESENTED,
            intent_type="ask_country",
            tool_results={
                "search_tours": {
                    "tours": [],
                    "query_echo": {"country_id": 2},
                }
            },
            customer_memory={},
            llm=llm,
        )
        assert rd.decision == "fallback_safe_conversation"
        assert rd.used_canned is True
        assert rd.used_llm is False
        assert rd.text != CANNED_HANDOFF_GENERIC
        assert "15" not in rd.text
        assert "low_risk_llm_fallback" in rd.notes

    def test_llm_error_for_known_country_and_budget_does_not_ask_budget_again(self):
        llm = _FailingLLM()
        rd = write_response(
            state=State.OPTIONS_PRESENTED,
            intent_type="ask_country",
            tool_results={
                "search_tours": {
                    "tours": [],
                    "query_echo": {"country_id": 2, "budget": 30000},
                }
            },
            customer_memory={"budget_per_person": 30000},
            llm=llm,
        )
        assert rd.decision == "fallback_safe_conversation"
        assert rd.used_canned is True
        assert rd.used_llm is False
        assert "30,000" in rd.text
        assert "ขอทราบงบ" not in rd.text
        assert "ช่วงเดือน" in rd.text

    def test_llm_error_for_known_country_budget_and_period_does_not_ask_period_again(self):
        llm = _FailingLLM()
        country = "ญี่ปุ่น"
        period = "เดือนหน้า"
        rd = write_response(
            state=State.OPTIONS_PRESENTED,
            intent_type="ask_period",
            tool_results={
                "search_tours": {
                    "tours": [],
                    "query_echo": {
                        "country_id": 2,
                        "country": country,
                        "budget": 30000,
                        "travel_period": period,
                    },
                }
            },
            customer_memory={
                "latest_country": country,
                "budget_per_person": 30000,
                "travel_month": period,
            },
            llm=llm,
        )
        assert rd.decision == "fallback_safe_conversation"
        assert rd.used_canned is True
        assert rd.used_llm is False
        assert "30,000" in rd.text
        assert period in rd.text
        assert "ขอทราบช่วงเดือน" not in rd.text

    def test_search_tours_omits_missing_dash_fields(self):
        llm = _DummyLLM()
        rd = write_response(
            state=State.OPTIONS_PRESENTED,
            intent_type="ask_country",
            tool_results={
                "search_tours": {
                    "tours": [
                        {
                            "rank": 1,
                            "web_code": "ap444",
                            "tour_code_real": None,
                            "name": "Tokyo Real 5D",
                            "price": 19999,
                            "days": 5,
                            "airline": None,
                            "url": "https://www.tourfiremai.com/tour/ap444",
                        }
                    ],
                    "query_echo": {"country_id": 2},
                }
            },
            customer_memory={},
            llm=llm,
        )
        assert rd.used_canned is True
        assert "ap444" in rd.text
        assert "รหัสทัวร์: -" not in rd.text
        assert "สายการบิน -" not in rd.text


# --- LLM-path tests -----------------------------------------------------------

class TestLLMPath:
    def test_new_lead_uses_llm(self):
        llm = _DummyLLM()
        rd = write_response(state=State.NEW_LEAD, intent_type="greeting",
                              tool_results={"raw_customer_text": "สวัสดี"},
                              customer_memory={}, llm=llm)
        assert rd.text is not None
        assert rd.used_llm is True
        assert rd.decision == "llm_reply"
        assert len(llm.call_log) == 1

    def test_wholesale_stripped_before_llm(self):
        llm = _DummyLLM()
        write_response(
            state=State.OPTIONS_PRESENTED, intent_type="ask_tour_detail",
            tool_results={"tours": [{"name": "X", "wholesale": "GS"}]},
            customer_memory={}, llm=llm,
        )
        # Look at what we sent to LLM
        sent = llm.last_user_text
        assert "GS" not in sent
        assert "wholesale" not in sent

    def test_brand_leak_in_reply_falls_back_to_canned(self):
        """If LLM hallucinates wholesale brand -> response writer must catch + fallback."""
        llm = _DummyLLM(next_text="ทัวร์นี้ของ TTN เลยค่ะ")  # contains brand!
        rd = write_response(state=State.NEW_LEAD, intent_type="greeting",
                              tool_results={}, customer_memory={}, llm=llm)
        assert rd.brand_leak_detected is True
        assert rd.text == CANNED_HANDOFF_GENERIC
        assert rd.decision == "fallback_canned"

    def test_silent_marker_returns_none(self):
        llm = _DummyLLM(next_text="__SILENT__")
        rd = write_response(state=State.NEW_LEAD, intent_type="greeting",
                              tool_results={}, customer_memory={}, llm=llm)
        assert rd.text is None

    def test_length_capped_at_400(self):
        long = "x" * 1000
        llm = _DummyLLM(next_text=long)
        rd = write_response(state=State.NEW_LEAD, intent_type="greeting",
                              tool_results={}, customer_memory={}, llm=llm)
        assert len(rd.text) <= 400
