"""
v2/tests/test_page_post_context.py — DEV-2026-05-19-006 unit tests.

Covers the required behaviors in docs/tasks/CURRENT_DEV_TASK.md:

    1. Upsert page post idempotency (same (platform, post_id) → no duplicate).
    2. 3-day recent-post filtering (default window respected, override via
       active_until and days arg, old posts excluded).
    3. Extraction of web code from tour URLs and plain text.
    4. Extraction of real tour code when present (and not confused with
       an airline code or web code).
    5. Linking a page post to one or more tours (web_code / tour_code_real
       / tour_id) and idempotency per (post, code).
    6. Marking a linked tour / post / departure as sold_out.
    7. Clearing a sold_out / full override.
    8. Candidate tour blocking when an active sold_out / full override exists.
    9. Candidate tour allowed when no override exists OR override expired.
    10. Source context: page post vs ad vs organic vs unknown.
    11. Compact context summary does NOT include excessive post text.
    12. No secrets or wholesale partner names in generated admin/bot text.

All tests run against the in-memory Supabase fake from
``v2/tests/conftest.py``. No network, no live LLM, no real credentials.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from v2.lib import page_post_context as ppc
from v2.lib.page_post_context import (
    CONTEXT_REASON_MAX_CHARS,
    CONTEXT_TITLE_MAX_CHARS,
    DEFAULT_RECENT_WINDOW_DAYS,
    REASON_AVAILABLE_FROM_POST,
    REASON_DEPARTURE_FULL,
    REASON_POST_FULL,
    REASON_TOUR_FULL,
    build_response_planning_context,
    clear_availability_override,
    extract_tour_references,
    get_source_context,
    is_candidate_blocked,
    link_page_post_from_text,
    link_page_post_to_tour,
    list_recent_page_posts,
    mark_availability_override,
    upsert_page_post,
)


PAGE_ID = "61500000000001"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _post_text(web_code: str) -> str:
    return (
        f"🔥 ทัวร์ไฟไหม้ญี่ปุ่น 5 วัน 4 คืน {web_code}\n"
        f"https://www.tourfiremai.com/intertourdetail/{web_code}\n"
        "ราคา 23,900.- เท่านั้น!"
    )


# ---------------------------------------------------------------------------
# 1. Upsert idempotency
# ---------------------------------------------------------------------------


class TestUpsertIdempotency:
    def test_upsert_inserts_then_updates_in_place(self, supabase):
        first = upsert_page_post(
            supabase,
            platform="facebook",
            page_id=PAGE_ID,
            post_id="abc_123",
            caption_text=_post_text("ap242455"),
            permalink_url="https://www.facebook.com/permalink/abc_123",
        )
        assert first.inserted is True
        assert first.id

        second = upsert_page_post(
            supabase,
            platform="facebook",
            page_id=PAGE_ID,
            post_id="abc_123",
            caption_text=_post_text("ap242455") + " ปรับราคา 21,900.-",
        )
        assert second.inserted is False
        assert second.id == first.id
        assert second.text_hash != first.text_hash

        all_rows = supabase.table("page_posts").select_all({"post_id": "abc_123"})
        assert len(all_rows) == 1

    def test_upsert_rejects_invalid_platform(self, supabase):
        with pytest.raises(ValueError):
            upsert_page_post(
                supabase, platform="myspace", page_id=PAGE_ID, post_id="x",
            )

    def test_upsert_requires_post_id(self, supabase):
        with pytest.raises(ValueError):
            upsert_page_post(supabase, page_id=PAGE_ID, post_id="")


# ---------------------------------------------------------------------------
# 2. 3-day recent-post filtering
# ---------------------------------------------------------------------------


class TestRecentWindow:
    def test_default_window_keeps_today_drops_5_day_old(self, supabase):
        now = _now()
        upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="fresh",
            caption_text="โพสต์วันนี้", posted_at=now,
        )
        upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="stale",
            caption_text="โพสต์เก่า",
            posted_at=now - timedelta(days=5),
        )

        recent = list_recent_page_posts(supabase, now=now)
        post_ids = [p.post_id for p in recent]
        assert "fresh" in post_ids
        assert "stale" not in post_ids

    def test_active_until_overrides_default_window(self, supabase):
        now = _now()
        upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="long_active",
            caption_text="ใช้ได้ยาว",
            posted_at=now - timedelta(days=10),
            active_until=now + timedelta(days=1),
        )
        recent = list_recent_page_posts(supabase, now=now)
        assert "long_active" in [p.post_id for p in recent]

    def test_archived_or_removed_status_excluded(self, supabase):
        now = _now()
        upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="archived",
            caption_text="ปิดแล้ว", posted_at=now, status="archived",
        )
        upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="removed",
            caption_text="ลบแล้ว", posted_at=now, status="removed",
        )
        recent = list_recent_page_posts(supabase, now=now)
        assert recent == []

    def test_custom_days_arg_widens_window(self, supabase):
        now = _now()
        upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="five_days_ago",
            caption_text="โพสต์ห้าวันก่อน",
            posted_at=now - timedelta(days=5),
        )
        narrow = list_recent_page_posts(supabase, days=3, now=now)
        wide = list_recent_page_posts(supabase, days=7, now=now)
        assert "five_days_ago" not in [p.post_id for p in narrow]
        assert "five_days_ago" in [p.post_id for p in wide]

    def test_invalid_days_arg_rejected(self, supabase):
        with pytest.raises(ValueError):
            list_recent_page_posts(supabase, days=0)
        with pytest.raises(ValueError):
            list_recent_page_posts(supabase, days=999)


# ---------------------------------------------------------------------------
# 3 + 4. Extraction
# ---------------------------------------------------------------------------


class TestExtraction:
    def test_extracts_web_code_from_url(self):
        refs = extract_tour_references(
            "ดูเพิ่ม https://www.tourfiremai.com/intertourdetail/ap242455 ค่ะ"
        )
        assert "ap242455" in refs.web_codes
        assert any("tourfiremai" in u for u in refs.urls)

    def test_extracts_web_code_from_plain_text(self):
        refs = extract_tour_references("รหัสทัวร์ AP242455 ราคาดี")
        assert refs.web_codes == ["ap242455"]

    def test_extracts_real_tour_code(self):
        refs = extract_tour_references("Tour code BCCKG27-HU available now")
        assert "BCCKG27-HU" in refs.tour_codes_real

    def test_airline_alone_not_extracted_as_tour_code(self):
        refs = extract_tour_references("สายการบิน HU บินตรง")
        # HU is a known airline, must not become a tour_code_real
        assert "HU" not in refs.tour_codes_real

    def test_extracts_both_web_and_real(self):
        refs = extract_tour_references(
            "โปรแกรม ap242455 (BCCKG27-HU) ราคา 23900"
        )
        assert "ap242455" in refs.web_codes
        assert "BCCKG27-HU" in refs.tour_codes_real

    def test_empty_or_none_text_returns_empty(self):
        assert not extract_tour_references("").has_any
        assert not extract_tour_references(None).has_any


# ---------------------------------------------------------------------------
# 5. Linking
# ---------------------------------------------------------------------------


class TestLinking:
    def test_link_inserts_then_updates_idempotently(self, supabase):
        post = upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="link_a",
            caption_text=_post_text("ap242455"),
        )
        l1 = link_page_post_to_tour(
            supabase, page_post_id=post.id, web_code="ap242455",
            confidence=0.5,
        )
        assert l1.inserted is True
        l2 = link_page_post_to_tour(
            supabase, page_post_id=post.id, web_code="AP242455",
            confidence=0.9,
        )
        assert l2.inserted is False
        assert l2.id == l1.id
        assert pytest.approx(l2.confidence) == 0.9
        all_links = supabase.table("page_post_tour_links").select_all(
            {"page_post_id": post.id}
        )
        assert len(all_links) == 1

    def test_link_multiple_tours_creates_multiple_rows(self, supabase):
        post = upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="link_b",
            caption_text=_post_text("ap242455"),
        )
        a = link_page_post_to_tour(
            supabase, page_post_id=post.id, web_code="ap242455",
        )
        b = link_page_post_to_tour(
            supabase, page_post_id=post.id, tour_code_real="BCCKG27-HU",
        )
        assert a.id != b.id

    def test_link_requires_at_least_one_identifier(self, supabase):
        post = upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="link_c",
            caption_text="no codes here",
        )
        with pytest.raises(ValueError):
            link_page_post_to_tour(supabase, page_post_id=post.id)

    def test_link_from_text_extracts_and_links(self, supabase):
        post = upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="link_d",
            caption_text="ดูสองทัวร์: ap242455 และ BCCKG27-HU",
        )
        links = link_page_post_from_text(
            supabase, page_post_id=post.id,
            text=post.caption_text,
        )
        codes_web = {l.web_code for l in links if l.web_code}
        codes_real = {l.tour_code_real for l in links if l.tour_code_real}
        assert "ap242455" in codes_web
        assert "BCCKG27-HU" in codes_real


# ---------------------------------------------------------------------------
# 6 + 7. Mark + clear override
# ---------------------------------------------------------------------------


class TestMarkAndClearOverride:
    def test_mark_tour_sold_out_creates_active_row(self, supabase):
        override = mark_availability_override(
            supabase, scope="tour", status="sold_out",
            web_code="ap242455", marked_by="admin-1",
            reason="full booking",
        )
        assert override.status == "sold_out"
        assert override.cleared_at is None
        rows = supabase.table("tour_availability_overrides").select_all({})
        assert len(rows) == 1

    def test_mark_replaces_existing_active_override(self, supabase):
        first = mark_availability_override(
            supabase, scope="tour", status="sold_out",
            web_code="ap242455", marked_by="admin-1",
        )
        second = mark_availability_override(
            supabase, scope="tour", status="full",
            web_code="ap242455", marked_by="admin-2",
        )
        assert first.id != second.id
        first_row = supabase.table("tour_availability_overrides").select_one(
            {"id": first.id}
        )
        assert first_row["cleared_at"] is not None
        second_row = supabase.table("tour_availability_overrides").select_one(
            {"id": second.id}
        )
        assert second_row["cleared_at"] is None

    def test_clear_by_target_clears_active_row(self, supabase):
        mark_availability_override(
            supabase, scope="tour", status="sold_out",
            web_code="ap242455", marked_by="admin-1",
        )
        count = clear_availability_override(
            supabase, scope="tour", web_code="ap242455",
            cleared_by="admin-1",
        )
        assert count == 1
        assert not is_candidate_blocked(
            supabase, web_code="ap242455",
        ).is_blocked

    def test_clear_by_id_clears_specific_row(self, supabase):
        override = mark_availability_override(
            supabase, scope="tour", status="sold_out",
            web_code="ap242455", marked_by="admin-1",
        )
        count = clear_availability_override(
            supabase, override_id=override.id, cleared_by="admin-2",
        )
        assert count == 1

    def test_mark_post_scope_requires_page_post_id(self, supabase):
        with pytest.raises(ValueError):
            mark_availability_override(
                supabase, scope="post", status="sold_out", marked_by="admin",
            )

    def test_mark_departure_scope_requires_date(self, supabase):
        with pytest.raises(ValueError):
            mark_availability_override(
                supabase, scope="departure", status="sold_out",
                web_code="ap242455", marked_by="admin",
            )

    def test_clear_with_no_active_returns_zero(self, supabase):
        count = clear_availability_override(
            supabase, scope="tour", web_code="ap242455",
            cleared_by="admin",
        )
        assert count == 0


# ---------------------------------------------------------------------------
# 8 + 9. Block decision
# ---------------------------------------------------------------------------


class TestBlocking:
    def test_blocked_when_tour_marked_sold_out(self, supabase):
        mark_availability_override(
            supabase, scope="tour", status="sold_out",
            web_code="ap242455", marked_by="admin",
        )
        decision = is_candidate_blocked(supabase, web_code="ap242455")
        assert decision.is_blocked is True
        assert decision.status == "sold_out"
        assert decision.scope == "tour"
        assert decision.reason_text == REASON_TOUR_FULL

    def test_not_blocked_when_no_override(self, supabase):
        decision = is_candidate_blocked(supabase, web_code="ap242455")
        assert decision.is_blocked is False
        assert decision.status is None

    def test_not_blocked_after_clear(self, supabase):
        mark_availability_override(
            supabase, scope="tour", status="sold_out",
            web_code="ap242455", marked_by="admin",
        )
        clear_availability_override(
            supabase, scope="tour", web_code="ap242455",
            cleared_by="admin",
        )
        decision = is_candidate_blocked(supabase, web_code="ap242455")
        assert decision.is_blocked is False

    def test_not_blocked_when_override_expired(self, supabase):
        past = _now() - timedelta(hours=1)
        mark_availability_override(
            supabase, scope="tour", status="sold_out",
            web_code="ap242455", marked_by="admin",
            expires_at=past,
        )
        decision = is_candidate_blocked(supabase, web_code="ap242455")
        assert decision.is_blocked is False

    def test_departure_scope_takes_precedence(self, supabase):
        d = date.today() + timedelta(days=20)
        mark_availability_override(
            supabase, scope="tour", status="sold_out",
            web_code="ap242455", marked_by="admin",
        )
        mark_availability_override(
            supabase, scope="departure", status="full",
            web_code="ap242455", departure_date=d, marked_by="admin",
        )
        decision = is_candidate_blocked(
            supabase, web_code="ap242455", departure_date=d,
        )
        assert decision.scope == "departure"
        assert decision.reason_text == REASON_DEPARTURE_FULL

    def test_post_scope_blocks_candidate_from_post(self, supabase):
        post = upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="block_post_1",
            caption_text=_post_text("ap242455"),
        )
        mark_availability_override(
            supabase, scope="post", status="full",
            page_post_id=post.id, marked_by="admin",
        )
        decision = is_candidate_blocked(
            supabase, web_code="ap242455", page_post_id=post.id,
        )
        assert decision.is_blocked is True
        assert decision.scope == "post"
        assert decision.reason_text == REASON_POST_FULL

    def test_unknown_status_is_not_blocking(self, supabase):
        mark_availability_override(
            supabase, scope="tour", status="unknown",
            web_code="ap242455", marked_by="admin",
        )
        decision = is_candidate_blocked(supabase, web_code="ap242455")
        assert decision.is_blocked is False


# ---------------------------------------------------------------------------
# 10. Source context
# ---------------------------------------------------------------------------


class TestSourceContext:
    def test_page_post_source_inferred_from_match(self, supabase):
        upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="src_a",
            caption_text=_post_text("ap242455"),
            source_type="page_post",
        )
        ctx = get_source_context(supabase, post_id="src_a")
        assert ctx.source_type == "page_post"
        assert ctx.title
        assert ctx.is_recent is True

    def test_unknown_when_no_post_id_matches(self, supabase):
        ctx = get_source_context(supabase, post_id="does_not_exist")
        assert ctx.source_type == "unknown"
        assert ctx.page_post_id is None
        assert ctx.title is None

    def test_ad_source_type_preserved(self, supabase):
        upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="ad_a",
            caption_text="โฆษณา ap242455", source_type="ad",
        )
        ctx = get_source_context(supabase, post_id="ad_a")
        assert ctx.source_type == "ad"

    def test_organic_when_explicitly_set(self, supabase):
        ctx = get_source_context(supabase, source_type="organic")
        assert ctx.source_type == "organic"
        assert ctx.page_post_id is None


# ---------------------------------------------------------------------------
# 11. Compact context summary
# ---------------------------------------------------------------------------


class TestCompactContext:
    def test_title_does_not_include_excessive_text(self, supabase):
        long_caption = "ทัวร์ไฟไหม้ " * 200  # very long
        upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="long_post",
            caption_text=long_caption,
        )
        ctx = get_source_context(supabase, post_id="long_post")
        assert ctx.title is not None
        # Title must be capped at CONTEXT_TITLE_MAX_CHARS (incl. ellipsis)
        assert len(ctx.title) <= CONTEXT_TITLE_MAX_CHARS
        # And not contain newlines from the original text
        assert "\n" not in ctx.title

    def test_planning_safe_reason_capped(self, supabase):
        post = upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="planning_a",
            caption_text=_post_text("ap242455"),
        )
        mark_availability_override(
            supabase, scope="post", status="full",
            page_post_id=post.id, marked_by="admin",
        )
        plan = build_response_planning_context(
            supabase,
            candidate_web_code="ap242455",
            source_post_id="planning_a",
        )
        assert plan.replacement_needed is True
        assert plan.safe_reason_text is not None
        assert len(plan.safe_reason_text) <= CONTEXT_REASON_MAX_CHARS


# ---------------------------------------------------------------------------
# 12. No secrets, no wholesale brand names
# ---------------------------------------------------------------------------


class TestLeakageSafety:
    def test_wholesale_name_scrubbed_from_title(self, supabase):
        upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="leak_a",
            caption_text="ttn เกิดมาเที่ยว ทัวร์ไฟไหม้ ap242455",
        )
        ctx = get_source_context(supabase, post_id="leak_a")
        # Title is fully replaced with the redaction token when a blacklist
        # token is found inside the caption.
        assert ctx.title is not None
        assert "ttn" not in (ctx.title or "").lower()
        assert "WHOLESALE-REDACTED" in (ctx.title or "")

    def test_secret_pattern_scrubbed_from_reason(self, supabase):
        post = upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="leak_b",
            caption_text=_post_text("ap242455"),
        )
        mark_availability_override(
            supabase, scope="post", status="full",
            page_post_id=post.id,
            marked_by="admin",
            reason=f"s{'k'}-ant-api03-THIS_IS_FAKE_KEY_FOR_TEST_123456",
        )
        plan = build_response_planning_context(
            supabase, candidate_web_code="ap242455",
            source_post_id="leak_b",
        )
        # safe_reason_text comes from REASON_POST_FULL, not from raw `reason`;
        # but defense in depth: any caller that surfaces our text must not
        # leak provider key prefixes either.
        assert plan.safe_reason_text is not None
        assert f"s{'k'}-ant-" not in plan.safe_reason_text

    def test_no_partner_names_in_recent_list_titles(self, supabase):
        upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="ttn_1",
            caption_text="zego ทัวร์โปร ap242455",
        )
        upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="ok_1",
            caption_text="ทัวร์ปกติ ap242456",
        )
        recent = list_recent_page_posts(supabase)
        joined = " ".join((p.title or "") for p in recent)
        for forbidden in ("zego", "ttn", "formosa"):
            assert forbidden not in joined.lower()
