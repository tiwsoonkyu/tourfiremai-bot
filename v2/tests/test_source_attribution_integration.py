"""
v2/tests/test_source_attribution_integration.py — DEV-2026-05-19-008.

Integration coverage: source_attribution.extract_source() → Orchestrator
handle_turn(). Proves that:

    1. A validated `source_post_id` reaches the planner so post-scope
       overrides block the recommendation.
    2. An unverified `source_post_id` (no matching `page_posts` row) is
       dropped at the boundary and does NOT block the recommendation.
    3. An ad-attributed event flows through with `source_type='ad'` and
       no post-scope block applies.

All in-memory fakes — no live Meta / FB / OpenAI / LINE / Supabase.
"""

from __future__ import annotations

import pytest

from v2.lib import page_post_context as ppc
from v2.lib.llm import MockLLMClient
from v2.lib.orchestrator import Orchestrator
from v2.lib.page_post_context import (
    mark_availability_override,
    upsert_page_post,
)
from v2.lib.source_attribution import extract_source


PAGE_ID = "61500000000001"
ADMIN_ID = "line-admin-1"


class _RecordingLLM(MockLLMClient):
    def __init__(self):
        super().__init__()
        self.response_calls: list[dict] = []
        self.next_text = "ขอเสนอตัวเลือกที่ตรงงบนะคะ"

    def chat(self, **kw):  # type: ignore[override]
        rsp = super().chat(**kw)
        if kw.get("tier") == "response":
            self.response_calls.append(kw)
            rsp.text = self.next_text
        return rsp


def _seed_selected_tour(supabase, *, psid, web_code, state="tour_selected"):
    cust = supabase.table("customers").insert({"psid": psid})
    conv = supabase.table("conversations").insert({
        "customer_id": cust["id"], "psid": psid, "state": state,
    })
    tour = supabase.table("tours_canonical").select_one({"web_code": web_code})
    supabase.table("selected_tours").insert({
        "conversation_id": conv["id"], "customer_id": cust["id"],
        "psid": psid, "tour_id": tour["id"],
        "tour_code_real": tour.get("tour_code_real"),
    })
    return cust, conv


class TestSourceAttributionToOrchestrator:
    def test_validated_post_blocks_candidate(self, supabase, redis, make_tour):
        make_tour(
            web_code="ap908001", name="ทัวร์โพสต์เต็ม",
            price=22000, country="ญี่ปุ่น", country_id=2,
        )
        post = upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="fb_post_attr_full",
            caption_text="ap908001 โพสต์ทดสอบ",
        )
        mark_availability_override(
            supabase, scope="post", status="full",
            page_post_id=post.id, marked_by=ADMIN_ID,
        )

        _seed_selected_tour(supabase, psid="PSID_ATTR_FULL", web_code="ap908001")

        # Build a Messenger-shaped event whose ref points at the known post.
        event = {
            "sender": {"id": "PSID_ATTR_FULL"},
            "message": {"mid": "m_x", "text": "ขอข้อมูลโพสต์นี้"},
            "referral": {"ref": "POST:fb_post_attr_full", "source": "SHORTLINK"},
        }
        attr = extract_source(event, supabase)
        assert attr.source_type == "page_post"
        assert attr.page_post_validated is True

        llm = _RecordingLLM()
        orch = Orchestrator(supabase, redis, llm)
        result = orch.handle_turn(
            psid="PSID_ATTR_FULL",
            text="ขอข้อมูลโพสต์นี้",
            meta_message_id="fb:attr_full_1",
            **attr.to_orchestrator_kwargs(),
        )
        assert result.decision == "canned_blocked"
        assert result.reply_text == ppc.REASON_POST_FULL
        assert llm.response_calls == []

    def test_unverified_post_id_does_not_block(self, supabase, redis, make_tour):
        make_tour(
            web_code="ap908002", name="ทัวร์ปกติ",
            price=20000, country="ญี่ปุ่น", country_id=2,
        )
        _seed_selected_tour(
            supabase, psid="PSID_ATTR_FAKE", web_code="ap908002",
            state="options_presented",
        )

        # Attacker-supplied post id; no row in DB → unverified → dropped.
        event = {
            "sender": {"id": "PSID_ATTR_FAKE"},
            "message": {"mid": "m_y", "text": "ขอตัวเลือก"},
            "source_type": "page_post",
            "source_post_id": "fake_post_that_does_not_exist",
        }
        attr = extract_source(event, supabase)
        assert attr.page_post_validated is False
        assert attr.to_orchestrator_kwargs()["source_post_id"] is None

        llm = _RecordingLLM()
        llm.next_text = "ยินดีค่ะ มีตัวเลือก ap908002 ตรงงบ"
        orch = Orchestrator(supabase, redis, llm)
        result = orch.handle_turn(
            psid="PSID_ATTR_FAKE",
            text="ขอตัวเลือก",
            meta_message_id="fb:attr_fake_1",
            **attr.to_orchestrator_kwargs(),
        )
        # Should NOT be canned-blocked since the fake post id was dropped.
        assert result.decision != "canned_blocked"
        # LLM was called for the non-blocked reply.
        assert len(llm.response_calls) >= 1

    def test_ad_attribution_flows_without_block(self, supabase, redis, make_tour):
        make_tour(
            web_code="ap908003", name="ทัวร์จากโฆษณา",
            price=21500, country="ญี่ปุ่น", country_id=2,
        )
        _seed_selected_tour(
            supabase, psid="PSID_ATTR_AD", web_code="ap908003",
            state="options_presented",
        )

        event = {
            "sender": {"id": "PSID_ATTR_AD"},
            "message": {"mid": "m_z", "text": "เห็นในโฆษณา"},
            "referral": {"source": "ADS", "ad_id": "ad_42"},
        }
        attr = extract_source(event, supabase)
        assert attr.source_type == "ad"
        kwargs = attr.to_orchestrator_kwargs()
        assert kwargs["source_type"] == "ad"
        assert kwargs["source_post_id"] is None

        llm = _RecordingLLM()
        llm.next_text = "ยินดีค่ะ"
        orch = Orchestrator(supabase, redis, llm)
        result = orch.handle_turn(
            psid="PSID_ATTR_AD",
            text="เห็นในโฆษณา",
            meta_message_id="fb:attr_ad_1",
            **kwargs,
        )
        assert result.decision != "canned_blocked"
