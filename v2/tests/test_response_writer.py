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
    def chat(self, **kw):
        rsp = super().chat(**kw)
        rsp.text = self.next_text
        return rsp


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
        sent = llm.call_log[0]["user_text"]
        assert "GS" not in sent
        assert "wholesale" not in sent

    def test_brand_leak_in_reply_falls_back_to_canned(self):
        """If LLM hallucinates wholesale brand → response writer must catch + fallback."""
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
