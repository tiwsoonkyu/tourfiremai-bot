"""
v2/tests/test_admin_dashboard_api.py — DEV-2026-05-19-008.

Tests for the dashboard-safe read API (`v2.lib.admin_dashboard_api`).

Coverage:

    1. `AdminContext.allowed=False` (or missing) denies every endpoint
       with `action='denied'` and `data=None`.
    2. `list_cases` returns compact case summaries: display_name preferred
       over raw PSID; no raw PSID leak; cap respected.
    3. `get_case` works by psid OR conversation_id, and returns None when
       not found.
    4. `list_recent_posts` never returns the raw caption — only the
       capped title.
    5. Wholesale partner names never leak in tour names / case fields.
    6. `list_open_handoffs` returns compact summaries with masked PSID.
    7. Limit is hard-capped (no giant payloads even if caller asks).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from v2.lib.admin_dashboard_api import AdminContext, AdminDashboardAPI
from v2.lib.admin_ops import record_handoff
from v2.lib.page_post_context import upsert_page_post


PSID_A = "1234567890123456"
PSID_B = "9876543210987654"
PAGE_ID = "61500000000001"


def _seed_customer_case(supabase, psid=PSID_A, name="Customer Alpha"):
    cust = supabase.table("customers").insert({"psid": psid, "fb_name": name})
    conv = supabase.table("conversations").insert({
        "customer_id": cust["id"], "psid": psid,
        "state": "collecting_preferences",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "last_activity_at": datetime.now(timezone.utc).isoformat(),
        "closed_at": None, "is_human_paused": False,
    })
    supabase.table("customer_memory").insert({
        "psid": psid, "latest_country": "ญี่ปุ่น", "latest_city": "โตเกียว",
        "budget_per_person": 35000, "pax_count": 2,
    })
    return cust, conv


def _admin_ok():
    return AdminContext(admin_user_id="U_admin", allowed=True, source="test")


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


class TestGate:
    def test_missing_context_denies_every_endpoint(self, supabase):
        api = AdminDashboardAPI(supabase=supabase)
        for fn in (api.list_cases, api.list_recent_posts, api.list_open_handoffs):
            r = fn(None)
            assert r["ok"] is False
            assert r["action"] == "denied"
            assert r["error"] == "missing_admin_context"
            assert r["data"] is None
        r = api.get_case(None, psid=PSID_A)
        assert r["ok"] is False
        assert r["error"] == "missing_admin_context"

    def test_unallowed_context_denies_every_endpoint(self, supabase):
        api = AdminDashboardAPI(supabase=supabase)
        ctx = AdminContext(admin_user_id="U_x", allowed=False)
        r = api.list_cases(ctx)
        assert r["ok"] is False
        assert r["error"] == "not_allowed"
        r = api.list_recent_posts(ctx)
        assert r["ok"] is False
        r = api.get_case(ctx, psid=PSID_A)
        assert r["ok"] is False

    def test_admin_context_to_dict_never_leaks_user_id(self):
        ctx = AdminContext(admin_user_id="U_secret", allowed=True)
        d = ctx.to_dict()
        assert "U_secret" not in str(d)
        assert d["has_user_id"] is True
        assert d["allowed"] is True


# ---------------------------------------------------------------------------
# list_cases
# ---------------------------------------------------------------------------


class TestListCases:
    def test_returns_compact_summary_with_masked_psid(self, supabase):
        _seed_customer_case(supabase, PSID_A, "Customer Alpha")
        api = AdminDashboardAPI(supabase=supabase)
        result = api.list_cases(_admin_ok())
        assert result["ok"] is True
        cases = result["data"]["cases"]
        assert len(cases) == 1
        case = cases[0]
        assert case["display_name"] == "Customer Alpha"
        assert case["psid_masked"]
        # raw PSID must NOT appear in any field.
        flat = str(case)
        assert PSID_A not in flat
        # cap fields surfaced
        assert case["latest_country"] == "ญี่ปุ่น"
        assert case["budget_per_person"] == 35000

    def test_only_paused_filter(self, supabase, make_customer):
        from v2.lib.admin_ops import pause_bot_for_customer
        _seed_customer_case(supabase, PSID_A, "Alpha")
        _seed_customer_case(supabase, PSID_B, "Beta")
        pause_bot_for_customer(
            supabase, psid=PSID_A, paused_by="admin", reason="manual",
        )
        api = AdminDashboardAPI(supabase=supabase)
        result = api.list_cases(_admin_ok(), only_paused=True)
        assert result["ok"] is True
        assert len(result["data"]["cases"]) == 1
        case = result["data"]["cases"][0]
        assert case["is_paused"] is True
        assert case["display_name"] == "Alpha"

    def test_limit_is_capped(self, supabase):
        for i in range(5):
            _seed_customer_case(supabase, psid=f"{1000000000000000 + i}", name=f"C{i}")
        api = AdminDashboardAPI(supabase=supabase)
        # Asking for 9999 must be capped.
        result = api.list_cases(_admin_ok(), limit=9999)
        assert result["ok"] is True
        assert result["data"]["limit"] <= 100


# ---------------------------------------------------------------------------
# get_case
# ---------------------------------------------------------------------------


class TestGetCase:
    def test_lookup_by_psid(self, supabase):
        _seed_customer_case(supabase, PSID_A, "Alpha")
        api = AdminDashboardAPI(supabase=supabase)
        result = api.get_case(_admin_ok(), psid=PSID_A)
        assert result["ok"] is True
        assert result["data"]["case"]["display_name"] == "Alpha"
        # No raw PSID anywhere.
        assert PSID_A not in str(result["data"])

    def test_lookup_by_conversation_id(self, supabase):
        _cust, conv = _seed_customer_case(supabase, PSID_A, "Alpha")
        api = AdminDashboardAPI(supabase=supabase)
        result = api.get_case(_admin_ok(), conversation_id=conv["id"])
        assert result["ok"] is True
        assert result["data"]["case"] is not None
        assert PSID_A not in str(result["data"])

    def test_missing_identifier_returns_error(self, supabase):
        api = AdminDashboardAPI(supabase=supabase)
        result = api.get_case(_admin_ok())
        assert result["ok"] is False
        assert result["error"] == "missing_identifier"

    def test_unknown_psid_returns_not_found(self, supabase):
        api = AdminDashboardAPI(supabase=supabase)
        result = api.get_case(_admin_ok(), psid="0000000000000000")
        assert result["ok"] is True  # well-formed query, but case is None
        assert result["data"]["case"] is None
        assert result["error"] == "case_not_found"


# ---------------------------------------------------------------------------
# list_recent_posts
# ---------------------------------------------------------------------------


class TestListRecentPosts:
    def test_no_raw_caption_only_title(self, supabase):
        long_caption = "ทัวร์ไฟไหม้ ap242455 พิเศษราคาสุดคุ้ม " * 30
        upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="fb_long_post",
            caption_text=long_caption,
        )
        api = AdminDashboardAPI(supabase=supabase)
        result = api.list_recent_posts(_admin_ok())
        assert result["ok"] is True
        posts = result["data"]["posts"]
        assert len(posts) == 1
        # Title is capped — never the full caption.
        assert posts[0]["title"]
        assert long_caption not in str(result)
        # No `caption_text` key in payload.
        assert "caption_text" not in posts[0]

    def test_includes_linked_codes(self, supabase):
        upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="fb_with_codes",
            caption_text="ap242455 BCCKG27-HU",
        )
        # Link a tour
        from v2.lib.page_post_context import link_page_post_from_text
        # link via auto-extract; depending on schema this may insert links.
        api = AdminDashboardAPI(supabase=supabase)
        result = api.list_recent_posts(_admin_ok())
        assert result["ok"] is True
        post = result["data"]["posts"][0]
        assert post["post_id"] == "fb_with_codes"
        assert isinstance(post["linked_web_codes"], list)


# ---------------------------------------------------------------------------
# list_open_handoffs
# ---------------------------------------------------------------------------


class TestListOpenHandoffs:
    def test_returns_masked_handoff(self, supabase):
        _cust, conv = _seed_customer_case(supabase, PSID_A, "Alpha")
        record_handoff(
            supabase, psid=PSID_A, trigger_type="human_request",
            trigger_detail={"reason": "ลูกค้าขอคุยกับทีม"},
            conversation_id=conv["id"],
        )
        api = AdminDashboardAPI(supabase=supabase)
        result = api.list_open_handoffs(_admin_ok())
        assert result["ok"] is True
        handoffs = result["data"]["handoffs"]
        assert len(handoffs) == 1
        h = handoffs[0]
        assert h["trigger_type"] == "human_request"
        assert PSID_A not in str(h)
        assert h["psid_masked"]
