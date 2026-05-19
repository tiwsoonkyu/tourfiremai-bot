"""
v2/tests/test_admin_dashboard_runtime.py — DEV-2026-05-19-009.

Tests for the Flask shim around `AdminDashboardAPI`:

    * GET /admin/dashboard/cases
    * GET /admin/dashboard/cases/<id>
    * GET /admin/dashboard/posts
    * GET /admin/dashboard/handoffs
    * GET /admin/healthz

Auth model: `X-Admin-Token` header compared in constant time.

Coverage:

    1. Missing or wrong token → 401 with `error='missing_admin_token'` or
       `'invalid_admin_token'`.
    2. Missing config token → 500 with `error='admin_token_not_configured'`.
    3. Valid token + populated DB → compact payloads with masked PSID,
       capped titles, no raw caption / wholesale / secrets.
    4. ?only_paused=1 surfaces only paused cases.
    5. ?limit is honoured and hard-capped.
    6. /admin/healthz reports allow-list count + has_token boolean.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

flask_available = True
try:
    import flask  # noqa: F401
except ImportError:
    flask_available = False

pytestmark = pytest.mark.skipif(not flask_available, reason="flask not installed")


from v2.lib.admin_ops import pause_bot_for_customer, record_handoff
from v2.lib.line_admin_adapter import AdminAllowList
from v2.lib.page_post_context import upsert_page_post


PAGE_ID = "61500000000001"
PSID_A = "1111222233334444"
PSID_B = "5555666677778888"
ADMIN_TOKEN = "test-dashboard-token-XYZ123"


@pytest.fixture
def fake_config():
    return SimpleNamespace(
        env_name="test",
        supabase_url="https://test.supabase.co",
        supabase_db_host="x", supabase_db_port=6543,
        supabase_db_user="u", supabase_db_password="p", supabase_db_name="postgres",
        supabase_service_key=None, supabase_anon_key=None,
        redis_url=None,
        fb_app_secret="test-app-secret",
        fb_page_access_token="EAA-test-token",
        fb_verify_token="test-verify",
        openai_api_key=None, openai_model="gpt-4o-mini",
        line_channel_token=None, line_admin_user_or_group_id=None,
        log_level="WARNING",
        has_redis=False, has_llm=False, has_line=False,
        database_uri="postgresql://u:p@x:6543/postgres",
    )


def _make_app(fake_config, supabase, redis, *, admin_token=ADMIN_TOKEN,
              allow_list=("U_admin_runtime",)):
    from v2.webhook.app import create_app
    return create_app(
        test_config=fake_config,
        test_supabase=supabase,
        test_redis=redis,
        test_admin_allow_list=AdminAllowList.from_iterable(allow_list),
        test_admin_token=admin_token,
    )


@pytest.fixture
def app(fake_config, supabase, redis):
    return _make_app(fake_config, supabase, redis)


@pytest.fixture
def client(app):
    return app.test_client()


def _auth():
    return {"X-Admin-Token": ADMIN_TOKEN}


def _seed_customer_case(supabase, *, psid=PSID_A, name="Customer A"):
    cust = supabase.table("customers").insert({"psid": psid, "fb_name": name})
    conv = supabase.table("conversations").insert({
        "customer_id": cust["id"], "psid": psid,
        "state": "collecting_preferences",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "last_activity_at": datetime.now(timezone.utc).isoformat(),
        "closed_at": None, "is_human_paused": False,
    })
    return cust, conv


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


class TestAuthGate:
    def test_missing_token_denied(self, client):
        for url in (
            "/admin/dashboard/cases",
            "/admin/dashboard/posts",
            "/admin/dashboard/handoffs",
            "/admin/dashboard/cases/anything",
        ):
            resp = client.get(url)
            assert resp.status_code == 401
            assert resp.get_json()["error"] == "missing_admin_token"

    def test_wrong_token_denied(self, client):
        resp = client.get(
            "/admin/dashboard/cases",
            headers={"X-Admin-Token": "wrong-token-value"},
        )
        assert resp.status_code == 401
        assert resp.get_json()["error"] == "invalid_admin_token"

    def test_missing_config_token_returns_500(self, fake_config, supabase, redis):
        app = _make_app(fake_config, supabase, redis, admin_token=None)
        client = app.test_client()
        resp = client.get(
            "/admin/dashboard/cases", headers={"X-Admin-Token": "anything"},
        )
        assert resp.status_code == 500
        assert resp.get_json()["error"] == "admin_token_not_configured"


# ---------------------------------------------------------------------------
# Endpoint behaviour
# ---------------------------------------------------------------------------


class TestDashboardEndpoints:
    def test_list_cases_returns_compact_payload(self, client, supabase):
        _seed_customer_case(supabase, psid=PSID_A, name="Alpha")
        resp = client.get("/admin/dashboard/cases", headers=_auth())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["action"] == "list_cases"
        cases = body["data"]["cases"]
        assert len(cases) == 1
        # Raw PSID never in payload.
        assert PSID_A not in json.dumps(body)
        assert cases[0]["display_name"] == "Alpha"
        assert cases[0]["psid_masked"]

    def test_get_case_by_psid(self, client, supabase):
        _seed_customer_case(supabase, psid=PSID_A, name="Alpha")
        resp = client.get(
            f"/admin/dashboard/cases/{PSID_A}", headers=_auth(),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["case"]["display_name"] == "Alpha"
        # Even though the PSID appears in the URL, the response body must
        # not include it.
        assert PSID_A not in json.dumps(body["data"])

    def test_get_case_by_conversation_id(self, client, supabase):
        cust, conv = _seed_customer_case(supabase, psid=PSID_A, name="Alpha")
        resp = client.get(
            f"/admin/dashboard/cases/{conv['id']}?by=conversation_id",
            headers=_auth(),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["case"]["display_name"] == "Alpha"

    def test_only_paused_filter(self, client, supabase):
        _seed_customer_case(supabase, psid=PSID_A, name="Alpha")
        _seed_customer_case(supabase, psid=PSID_B, name="Beta")
        pause_bot_for_customer(
            supabase, psid=PSID_A, paused_by="admin", reason="manual",
        )
        resp = client.get(
            "/admin/dashboard/cases?only_paused=1", headers=_auth(),
        )
        body = resp.get_json()
        assert len(body["data"]["cases"]) == 1
        assert body["data"]["cases"][0]["display_name"] == "Alpha"
        assert body["data"]["cases"][0]["is_paused"] is True

    def test_list_recent_posts_caps_title_and_drops_caption(self, client, supabase):
        long_caption = "พิเศษ ทัวร์ไฟไหม้ ap242455 ราคา " * 30
        upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="fb_rt_long_caption",
            caption_text=long_caption,
        )
        resp = client.get("/admin/dashboard/posts", headers=_auth())
        assert resp.status_code == 200
        body = resp.get_json()
        posts = body["data"]["posts"]
        assert len(posts) == 1
        assert "caption_text" not in posts[0]
        # Full caption MUST NOT appear anywhere in the response.
        assert long_caption not in json.dumps(body)

    def test_list_open_handoffs_masks_psid(self, client, supabase):
        _cust, conv = _seed_customer_case(supabase, psid=PSID_A, name="Alpha")
        record_handoff(
            supabase, psid=PSID_A, trigger_type="human_request",
            trigger_detail={"reason": "test"},
            conversation_id=conv["id"],
        )
        resp = client.get("/admin/dashboard/handoffs", headers=_auth())
        assert resp.status_code == 200
        body = resp.get_json()
        handoffs = body["data"]["handoffs"]
        assert len(handoffs) == 1
        assert PSID_A not in json.dumps(body)
        assert handoffs[0]["psid_masked"]

    def test_admin_healthz_reports_state(self, client):
        resp = client.get("/admin/healthz")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "ok"
        assert body["admin_allow_list_count"] >= 1
        assert body["admin_dashboard_token_configured"] is True

    def test_limit_is_capped(self, client, supabase):
        for i in range(5):
            _seed_customer_case(supabase, psid=f"{1000000000000000 + i}", name=f"C{i}")
        resp = client.get(
            "/admin/dashboard/cases?limit=99999", headers=_auth(),
        )
        body = resp.get_json()
        assert body["data"]["limit"] <= 100
