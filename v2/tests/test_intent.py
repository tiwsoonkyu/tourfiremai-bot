"""Sprint 2 test: rule-based intent classifier."""

import pytest
from v2.lib.intent import classify, classify_rule_based, Intent


class TestUniversalIntents:
    def test_attachment_dominates(self):
        i = classify_rule_based("hello", attachments=[{"type": "image"}])
        assert i.type == "send_attachment"
        assert i.has_attachment

    def test_ask_human(self):
        for text in ["ขอคุยกับคนได้ไหม", "ขอแอดมิน", "human please"]:
            assert classify_rule_based(text).type == "ask_human"

    def test_payment_keyword(self):
        for text in ["โอนยังไง", "บัญชีไหน", "สลิป", "PromptPay"]:
            assert classify_rule_based(text).type == "payment_keyword"


class TestCodeSelection:
    def test_web_code(self):
        i = classify_rule_based("ขอ ap242455")
        assert i.type == "select_tour"
        assert i.selected_code == "ap242455"

    def test_tour_code_real(self):
        i = classify_rule_based("รหัส BCCKG27-HU")
        assert i.type == "select_tour"
        assert i.selected_code == "BCCKG27-HU"


class TestIndexSelection:
    def test_thai_first(self):
        i = classify_rule_based("เอาตัวแรก")
        assert i.type == "select_tour"
        assert i.selected_index == 1

    def test_thai_numeric(self):
        i = classify_rule_based("ตัวที่ 2")
        assert i.type == "select_tour"
        assert i.selected_index == 2


class TestCriteria:
    def test_country(self):
        i = classify_rule_based("อยากไปญี่ปุ่น")
        assert i.country == "ญี่ปุ่น"
        assert i.country_id == 2
        assert i.type in ("ask_country", "ask_tour_detail")

    def test_country_with_budget_searches_country_first(self):
        i = classify_rule_based("มีทัวร์ไปญี่ปุ่นไหมครับ งบไม่เกิน 30000")
        assert i.type == "ask_country"
        assert i.country == "ญี่ปุ่น"
        assert i.country_id == 2
        assert i.budget == 30000
        assert i.budget_type == "strict"

    def test_budget_strict(self):
        i = classify_rule_based("ไม่เกิน 25000")
        assert i.budget == 25000
        assert i.budget_type == "strict"

    def test_budget_flex(self):
        i = classify_rule_based("ประมาณ 30,000")
        assert i.budget == 30000
        assert i.budget_type == "flexible"

    def test_pax(self):
        i = classify_rule_based("4 คน")
        assert i.pax_count == 4

    def test_fee_question(self):
        i = classify_rule_based("ค่าทิปเท่าไหร่")
        assert i.type == "ask_fee"


class TestGreetingAndFallback:
    def test_greeting(self):
        assert classify_rule_based("สวัสดีค่ะ").type == "greeting"

    def test_unknown(self):
        assert classify_rule_based("asdfgh").type == "unknown"

    def test_empty(self):
        assert classify_rule_based("").type == "unknown"


class TestLLMSafety:
    def test_no_llm_call_by_default(self):
        """Sprint 2: must NOT invoke LLM even when enable_llm=False."""
        sentinel_client = object()  # would explode if used
        i = classify("blah", enable_llm=False, llm_client=sentinel_client)
        assert isinstance(i, Intent)

    def test_high_confidence_skips_llm(self):
        # ap242455 → web_code match → confidence 1.0 → skip LLM
        i = classify("ap242455", enable_llm=True, llm_client="WOULD-FAIL")
        assert i.type == "select_tour"

    def test_llm_required_raises_in_s2(self):
        # Low confidence triggers LLM path which raises NotImplementedError
        # but classify() catches and returns rule_intent with notes
        i = classify("asdfgh???", enable_llm=True, llm_client=object())
        assert "llm_failed" in str(i.notes) or i.type == "unknown"
