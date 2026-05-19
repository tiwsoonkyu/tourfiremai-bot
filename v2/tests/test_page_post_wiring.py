"""
v2/tests/test_page_post_wiring.py — DEV-2026-05-19-007.

Covers:

    1. Admin command `posts` returns recent-post SUMMARIES only — capped
       titles, never raw captions.
    2. Admin command `mark_full <web_code>` calls the override service and
       returns a safe Thai admin-facing confirmation; data['override'] is
       populated.
    3. Admin command `clear_full <web_code>` clears the override.
    4. Admin command with airline-only target returns a safe clarification
       message and does NOT mutate.
    5. Response writer blocks a candidate tour marked `full` (planning
       passed in by caller); LLM is NOT called.
    6. Response writer blocks a candidate from a marked-full page post.
    7. Response writer allows a candidate (LLM is invoked) when no override
       exists.
    8. The planning_note injected into the LLM payload is compact: source
       type / title / replacement_needed only, no raw caption, no admin
       reason text.
    9. The blocked response decision uses the safe Thai canned text and
       never the LLM's text.
    10. No wholesale partner names or secret patterns leak in new
        admin-facing or bot-facing strings.

All tests use the in-memory Supabase fake (`v2/tests/conftest.py`) and the
`MockLLMClient`. No live Meta/FB/LINE/OpenAI/Supabase calls.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from v2.lib import page_post_context as ppc
from v2.lib.admin_command_handler import handle_admin_command, parse_admin_command
from v2.lib.llm import MockLLMClient
from v2.lib.page_post_context import (
    build_response_planning_context,
    mark_availability_override,
    upsert_page_post,
)
from v2.lib.response_writer import (
    CANNED_BLOCKED_REPLACEMENT,
    write_response,
)
from v2.lib.state_machine import State


PAGE_ID = "61500000000001"
ADMIN_ID = "line-admin-1"


def _seed_post(supabase, *, post_id: str, caption: str = "โพสต์ทดสอบ ap242455",
               source_type: str = "page_post"):
    return upsert_page_post(
        supabase, page_id=PAGE_ID, post_id=post_id,
        caption_text=caption, source_type=source_type,
    )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TestParser:
    def test_recognizes_new_commands(self):
        assert parse_admin_command("posts").action == "posts"
        assert parse_admin_command("post fb_123").action == "post"
        c = parse_admin_command("mark_full ap242455 admin override")
        assert c.action == "mark_full"
        assert c.target == "ap242455"
        assert c.reason == "admin override"
        c = parse_admin_command("mark_sold_out BCCKG27-HU")
        assert c.action == "mark_sold_out"
        assert c.target == "BCCKG27-HU"
        assert parse_admin_command("clear_full ap242455").action == "clear_full"
        assert parse_admin_command("clear_sold_out ap242455").action == "clear_sold_out"


# ---------------------------------------------------------------------------
# `posts` and `post` commands
# ---------------------------------------------------------------------------


class TestPostsCommand:
    def test_posts_returns_summaries_not_captions(self, supabase):
        long_caption = (
            "พิเศษ! ทัวร์ไฟไหม้ ap242455 รหัส BCCKG27-HU ราคา 19,900 "
            * 30
        )
        _seed_post(supabase, post_id="long_a", caption=long_caption)
        result = handle_admin_command("posts", supabase, admin_user_id=ADMIN_ID)
        assert result.ok is True
        assert result.action == "posts"
        # Long-caption text must NOT appear verbatim in admin output.
        assert long_caption not in result.admin_text
        # Each summary line stays well under 200 chars.
        for line in result.admin_text.splitlines()[1:]:
            assert len(line) < 200

    def test_posts_empty_state(self, supabase):
        result = handle_admin_command("posts", supabase, admin_user_id=ADMIN_ID)
        assert result.ok is True
        assert "ยังไม่มีรายการ" in result.admin_text
        assert result.data == {"posts": []}

    def test_post_detail_for_known_post(self, supabase):
        _seed_post(supabase, post_id="detail_a",
                   caption="โพสต์ ap242455 ราคา 23,900")
        result = handle_admin_command(
            "post detail_a", supabase, admin_user_id=ADMIN_ID,
        )
        assert result.ok is True
        assert "post_id: detail_a" in result.admin_text

    def test_post_detail_for_unknown_post(self, supabase):
        result = handle_admin_command(
            "post nope", supabase, admin_user_id=ADMIN_ID,
        )
        assert result.ok is False
        assert result.error == "post_not_found"


# ---------------------------------------------------------------------------
# mark_full / clear_full
# ---------------------------------------------------------------------------


class TestMarkAndClearCommands:
    def test_mark_full_web_code_creates_override(self, supabase):
        result = handle_admin_command(
            "mark_full ap242455 admin observed full",
            supabase, admin_user_id=ADMIN_ID,
        )
        assert result.ok is True
        assert result.mutated is True
        assert "ap242455" in result.admin_text
        assert result.data["override"]["status"] == "full"
        rows = supabase.table("tour_availability_overrides").select_all({})
        active = [r for r in rows if r.get("cleared_at") is None]
        assert len(active) == 1
        assert active[0]["status"] == "full"

    def test_mark_sold_out_tour_code_real(self, supabase):
        result = handle_admin_command(
            "mark_sold_out BCCKG27-HU", supabase, admin_user_id=ADMIN_ID,
        )
        assert result.ok is True
        assert result.mutated is True
        assert result.data["override"]["status"] == "sold_out"
        assert result.data["override"]["tour_code_real"] == "BCCKG27-HU"

    def test_clear_full_clears_active_override(self, supabase):
        handle_admin_command(
            "mark_full ap242455", supabase, admin_user_id=ADMIN_ID,
        )
        result = handle_admin_command(
            "clear_full ap242455", supabase, admin_user_id=ADMIN_ID,
        )
        assert result.ok is True
        assert result.mutated is True
        assert result.data["cleared"] == 1
        # idempotent: clearing again is a no-op with safe text
        again = handle_admin_command(
            "clear_full ap242455", supabase, admin_user_id=ADMIN_ID,
        )
        assert again.ok is True
        assert again.mutated is False
        assert again.data["cleared"] == 0

    def test_mark_full_post_id_uses_post_scope(self, supabase):
        post = _seed_post(supabase, post_id="block_me")
        result = handle_admin_command(
            "mark_full block_me holiday rush",
            supabase, admin_user_id=ADMIN_ID,
        )
        assert result.ok is True
        assert result.data["override"]["scope"] == "post"
        assert result.data["override"]["page_post_id"] == post.id

    def test_ambiguous_airline_target_refused(self, supabase):
        result = handle_admin_command(
            "mark_full HU", supabase, admin_user_id=ADMIN_ID,
        )
        assert result.ok is False
        assert result.error == "ambiguous_target"
        assert result.mutated is False
        assert "web_code" in result.admin_text or "tour code" in result.admin_text
        rows = supabase.table("tour_availability_overrides").select_all({})
        assert rows == []

    def test_unknown_post_id_returns_safe_message(self, supabase):
        result = handle_admin_command(
            "mark_full unknownpostid",
            supabase, admin_user_id=ADMIN_ID,
        )
        assert result.ok is False
        assert result.error == "target_not_found"
        assert result.mutated is False


# ---------------------------------------------------------------------------
# Response writer wiring
# ---------------------------------------------------------------------------


class _RecordingLLM(MockLLMClient):
    def __init__(self):
        super().__init__()
        self.called = False
        self.last_messages = None
        self.next_text = "(safe mock reply)"

    def chat(self, **kw):
        self.called = True
        self.last_messages = kw.get("messages")
        rsp = super().chat(**kw)
        rsp.text = self.next_text
        return rsp


class TestResponsePlanningWiring:
    def _customer_memory(self):
        return {"customer_name": "ลูกค้าทดสอบ"}

    def test_blocks_candidate_marked_full(self, supabase):
        mark_availability_override(
            supabase, scope="tour", status="full",
            web_code="ap242455", marked_by=ADMIN_ID,
        )
        planning = build_response_planning_context(
            supabase, candidate_web_code="ap242455",
        )
        assert planning.replacement_needed is True

        llm = _RecordingLLM()
        rd = write_response(
            state=State.OPTIONS_PRESENTED, intent_type="select_index",
            tool_results={"raw_customer_text": "เอาตัวที่ 1"},
            customer_memory=self._customer_memory(),
            llm=llm, planning=planning,
        )
        assert rd is not None
        assert rd.decision == "canned_blocked"
        assert rd.used_canned is True
        assert rd.used_llm is False
        # LLM must NOT have been called for the blocked path.
        assert llm.called is False
        # Reason text comes from the deterministic planner (page-post module).
        assert rd.text == ppc.REASON_TOUR_FULL

    def test_blocks_candidate_from_marked_full_post(self, supabase):
        post = _seed_post(supabase, post_id="src_post")
        mark_availability_override(
            supabase, scope="post", status="full",
            page_post_id=post.id, marked_by=ADMIN_ID,
        )
        planning = build_response_planning_context(
            supabase, candidate_web_code="ap242455",
            source_post_id="src_post",
        )
        assert planning.replacement_needed is True

        llm = _RecordingLLM()
        rd = write_response(
            state=State.OPTIONS_PRESENTED, intent_type="select_index",
            tool_results={"raw_customer_text": "เอาตัวในโพสต์"},
            customer_memory=self._customer_memory(),
            llm=llm, planning=planning,
        )
        assert rd.decision == "canned_blocked"
        assert llm.called is False
        assert rd.text == ppc.REASON_POST_FULL

    def test_allows_candidate_when_no_override(self, supabase):
        _seed_post(supabase, post_id="src_post_ok")
        planning = build_response_planning_context(
            supabase, candidate_web_code="ap242455",
            source_post_id="src_post_ok",
        )
        assert planning.replacement_needed is False

        llm = _RecordingLLM()
        llm.next_text = "ขอเสนอตัวเลือกที่ตรงงบนะคะ"
        rd = write_response(
            state=State.OPTIONS_PRESENTED, intent_type="select_index",
            tool_results={"raw_customer_text": "เอาตัวที่ 1"},
            customer_memory=self._customer_memory(),
            llm=llm, planning=planning,
        )
        assert rd.decision == "llm_reply"
        assert rd.used_llm is True
        assert llm.called is True
        # Compact planning_note must be present in the user payload.
        user_msg = next(
            m["content"] for m in llm.last_messages if m["role"] == "user"
        )
        assert "page_post_planning_note" in user_msg

    def test_planning_note_is_compact(self, supabase):
        long = "ttn ทัวร์ไฟไหม้ ap242455 พิเศษ " * 100
        _seed_post(supabase, post_id="compact_test", caption=long)
        planning = build_response_planning_context(
            supabase, candidate_web_code="ap242455",
            source_post_id="compact_test",
        )

        llm = _RecordingLLM()
        llm.next_text = "ทักทาย"
        write_response(
            state=State.COLLECTING_PREFERENCES, intent_type="greeting",
            tool_results={"raw_customer_text": "สวัสดี"},
            customer_memory=self._customer_memory(),
            llm=llm, planning=planning,
        )
        user_msg = next(
            m["content"] for m in llm.last_messages if m["role"] == "user"
        )
        # The full long caption must NEVER leak into the LLM payload.
        assert long not in user_msg
        # And no wholesale partner name either.
        assert "ttn" not in user_msg.lower()
        assert "WHOLESALE-REDACTED" in user_msg or "ttn" not in user_msg.lower()

    def test_blocked_does_not_use_llm_text(self, supabase):
        mark_availability_override(
            supabase, scope="tour", status="sold_out",
            web_code="ap242455", marked_by=ADMIN_ID,
        )
        planning = build_response_planning_context(
            supabase, candidate_web_code="ap242455",
        )
        llm = _RecordingLLM()
        llm.next_text = "DO NOT USE — should be ignored"
        rd = write_response(
            state=State.OPTIONS_PRESENTED, intent_type="select_index",
            tool_results={}, customer_memory=self._customer_memory(),
            llm=llm, planning=planning,
        )
        assert "DO NOT USE" not in (rd.text or "")
        assert llm.called is False


# ---------------------------------------------------------------------------
# Leakage safety
# ---------------------------------------------------------------------------


class TestLeakageSafety:
    def test_no_wholesale_name_in_posts_admin_output(self, supabase):
        _seed_post(supabase, post_id="leak_a",
                   caption="ttn ทัวร์ไฟไหม้ ap242455")
        result = handle_admin_command("posts", supabase, admin_user_id=ADMIN_ID)
        assert "ttn" not in result.admin_text.lower()
        assert "zego" not in result.admin_text.lower()

    def test_no_secret_in_mark_full_reason(self, supabase):
        fake = f"s{'k'}-ant-api03-THIS_IS_FAKE_TEST_KEY_1234567890"
        result = handle_admin_command(
            f"mark_full ap242455 {fake}",
            supabase, admin_user_id=ADMIN_ID,
        )
        # The actual secret PAYLOAD must be redacted; the redactor leaves
        # the bare "sk-ant-" prefix in place as an audit marker.
        assert "FAKE_TEST_KEY" not in result.admin_text
        assert "api03-THIS_IS" not in result.admin_text
        assert "REDACTED" in result.admin_text
