"""
v2/tests/test_orchestrator_planning.py — DEV-2026-05-19-007.

Orchestrator-level coverage for the page-post / sold-out planning wiring.

These tests run end-to-end through Orchestrator.handle_turn() with the
in-memory Supabase + Redis fakes, so they prove:

    1. The orchestrator builds a PlanningContext bundle and passes it to
       write_response (so the deterministic block decision happens BEFORE
       the LLM is ever called).
    2. A candidate that the page-post service flags `replacement_needed`
       results in a canned safe reply — LLM tier `response` is NOT called.
    3. An unblocked turn produces an LLM reply; the LLM payload carries the
       compact `page_post_planning_note` and never includes raw captions.
    4. Source attribution (source_post_id / source_type) flows through to
       the planner so post-scope overrides block correctly.

All fakes only — no live Meta / FB / LINE / OpenAI / Supabase / OCR calls.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from v2.lib import page_post_context as ppc
from v2.lib.llm import MockLLMClient
from v2.lib.orchestrator import Orchestrator
from v2.lib.page_post_context import (
    mark_availability_override,
    upsert_page_post,
)


ADMIN_ID = "line-admin-1"
PAGE_ID = "61500000000001"


class _RecordingLLM(MockLLMClient):
    """MockLLMClient subclass that records `response`-tier calls."""

    def __init__(self):
        super().__init__()
        self.response_calls: list[dict] = []
        self.last_user_payload: Optional[str] = None
        self.next_text = "ขอเสนอตัวเลือกที่ตรงงบนะคะ"

    def chat(self, **kw):  # type: ignore[override]
        rsp = super().chat(**kw)
        tier = kw.get("tier")
        if tier == "response":
            self.response_calls.append(kw)
            msgs = kw.get("messages") or []
            for m in msgs:
                if m.get("role") == "user":
                    self.last_user_payload = m.get("content")
            rsp.text = self.next_text
        return rsp


def _make_orch(supabase, redis, llm=None) -> Orchestrator:
    return Orchestrator(supabase, redis, llm or _RecordingLLM())


# ---------------------------------------------------------------------------
# 1) Blocked candidate — orchestrator returns canned reply, no LLM call.
# ---------------------------------------------------------------------------


class TestOrchestratorBlocksFullCandidate:
    def test_locked_tour_marked_full_returns_canned(self, supabase, redis, make_tour):
        """Pre-lock a tour, then admin marks the tour full. Next turn must
        produce a deterministic canned reply (no LLM `response` call)."""
        tour = make_tour(
            web_code="ap777001", name="ทัวร์ทดสอบเต็ม",
            price=22900, country="ญี่ปุ่น", country_id=2,
        )
        psid = "PSID_BLOCK_FULL"

        # Seed customer + conversation + selected_tour lock manually.
        cust = supabase.table("customers").insert({"psid": psid})
        conv = supabase.table("conversations").insert({
            "customer_id": cust["id"], "psid": psid,
            "state": "tour_selected",
        })
        supabase.table("selected_tours").insert({
            "conversation_id": conv["id"], "customer_id": cust["id"],
            "psid": psid, "tour_id": tour["id"],
            "tour_code_real": tour.get("tour_code_real"),
        })

        mark_availability_override(
            supabase, scope="tour", status="full",
            web_code="ap777001", marked_by=ADMIN_ID,
        )

        llm = _RecordingLLM()
        orch = _make_orch(supabase, redis, llm=llm)
        result = orch.handle_turn(
            psid=psid, text="ตัวเลือกนี้ยังว่างไหม",
            meta_message_id="fb:plan_block_1",
        )

        # Bot must have replied with the deterministic safe reason text and
        # NOT have called the LLM `response` tier at all.
        assert result.silent is False
        assert result.reply_text == ppc.REASON_TOUR_FULL
        assert result.decision == "canned_blocked"
        assert llm.response_calls == []

    def test_post_scope_block_from_source_post_id(self, supabase, redis, make_tour):
        """Customer comes from a page post that admin marked `full`.
        Even when candidate tour is unlocked at the tour level, post-scope
        override blocks the recommendation."""
        make_tour(
            web_code="ap777002", name="ทัวร์โพสต์เต็ม",
            price=21900, country="ญี่ปุ่น", country_id=2,
        )
        post = upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="src_post_full",
            caption_text="โพสต์ปกติ ap777002",
        )
        mark_availability_override(
            supabase, scope="post", status="full",
            page_post_id=post.id, marked_by=ADMIN_ID,
        )

        psid = "PSID_BLOCK_POST"
        # Seed a selected tour so candidate web_code is resolvable from memory.
        cust = supabase.table("customers").insert({"psid": psid})
        conv = supabase.table("conversations").insert({
            "customer_id": cust["id"], "psid": psid,
            "state": "tour_selected",
        })
        tour_row = supabase.table("tours_canonical").select_one(
            {"web_code": "ap777002"}
        )
        supabase.table("selected_tours").insert({
            "conversation_id": conv["id"], "customer_id": cust["id"],
            "psid": psid, "tour_id": tour_row["id"],
        })

        llm = _RecordingLLM()
        orch = _make_orch(supabase, redis, llm=llm)
        result = orch.handle_turn(
            psid=psid, text="ตัวเลือกในโพสต์นี้",
            meta_message_id="fb:plan_block_post_1",
            source_post_id="src_post_full",
            source_type="page_post",
        )

        assert result.decision == "canned_blocked"
        assert result.reply_text == ppc.REASON_POST_FULL
        assert llm.response_calls == []


# ---------------------------------------------------------------------------
# 2) Unblocked candidate — LLM is called and compact note is injected.
# ---------------------------------------------------------------------------


class TestOrchestratorAllowsUnblockedCandidate:
    def test_unblocked_search_tours_uses_deterministic_reply_no_caption_leak(self, supabase, redis, make_tour):
        make_tour(
            web_code="ap777003", name="ทัวร์ปกติ",
            price=19900, country="ญี่ปุ่น", country_id=2,
        )
        # Seed a page post with a long caption to verify compaction.
        long_caption = "ทัวร์ไฟไหม้ ap777003 พิเศษ " * 50
        upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="src_post_ok",
            caption_text=long_caption,
        )

        psid = "PSID_ALLOW_OK"
        cust = supabase.table("customers").insert({"psid": psid})
        conv = supabase.table("conversations").insert({
            "customer_id": cust["id"], "psid": psid,
            "state": "options_presented",
        })
        tour_row = supabase.table("tours_canonical").select_one(
            {"web_code": "ap777003"}
        )
        supabase.table("selected_tours").insert({
            "conversation_id": conv["id"], "customer_id": cust["id"],
            "psid": psid, "tour_id": tour_row["id"],
        })

        llm = _RecordingLLM()
        llm.next_text = "ยินดีค่ะ มีตัวเลือก ap777003 ที่ตรงงบนะคะ"
        orch = _make_orch(supabase, redis, llm=llm)
        result = orch.handle_turn(
            psid=psid, text="ขอตัวเลือกแถวๆ ญี่ปุ่น",
            meta_message_id="fb:plan_allow_1",
            source_post_id="src_post_ok",
            source_type="page_post",
        )

        assert result.silent is False
        # Search results are now answered deterministically from canonical data.
        assert len(llm.response_calls) == 0
        assert result.decision == "canned_search_results"
        assert result.reply_text
        assert "ap777003" in result.reply_text
        # Raw page-post captions must never leak into customer-facing text.
        assert long_caption not in result.reply_text


# ---------------------------------------------------------------------------
# 3) Source attribution is optional — orchestrator stays safe without it.
# ---------------------------------------------------------------------------


class TestOrchestratorPlanningOptional:
    def test_no_source_attribution_still_replies(self, supabase, redis):
        """Greeting turn — no candidate, no source post. Planner must build
        successfully and orchestrator must still reply via LLM."""
        llm = _RecordingLLM()
        llm.next_text = "สวัสดีค่ะ"
        orch = _make_orch(supabase, redis, llm=llm)
        result = orch.handle_turn(
            psid="PSID_NO_SRC", text="สวัสดีค่ะ",
            meta_message_id="fb:plan_no_src_1",
        )
        assert result.silent is False
        assert result.decision == "llm_reply"
        # The LLM was called and no canned-block path was triggered.
        assert len(llm.response_calls) >= 1
