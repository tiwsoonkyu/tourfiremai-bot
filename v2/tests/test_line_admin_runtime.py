"""
v2/tests/test_line_admin_runtime.py — DEV-2026-05-19-009.

Tests for the V2 LINE admin runtime entrypoint at `POST /admin/line`.

Coverage:

    1. Missing / non-allowlisted sender is denied; no side effects on
       bot_pauses / tour_availability_overrides.
    2. Authorized sender can run `cases`, `pause <id>`, `resume <id>`,
       `mark_full <web_code>`, `clear_full <web_code>`.
    3. Oversized request body is rejected with 413.
    4. Empty allow-list rejects even otherwise-allowed senders.
    5. Route never echoes the raw attacker-supplied text in denial.
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


from v2.lib.admin_ops import is_bot_paused_for
from v2.lib.line_admin_adapter import AdminAllowList


PSID_CUST = "1234567890999999"
ALLOWED = "U_admin_runtime"
DENIED = "U_random_stranger"


def _active_overrides(supabase, *, web_code=None):
    rows = supabase.table("tour_availability_overrides").select_all({}) \
        if hasattr(supabase.table("tour_availability_overrides"), "select_all") else []
    out = []
    for r in rows:
        if r.get("cleared_at"):
            continue
        if web_code is not None and r.get("web_code") != web_code:
            continue
        out.append(r)
    return out


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


def _make_app(fake_config, supabase, redis, *, allow_list=(ALLOWED,)):
    from v2.webhook.app import create_app
    return create_app(
        test_config=fake_config,
        test_supabase=supabase,
        test_redis=redis,
        test_admin_allow_list=AdminAllowList.from_iterable(allow_list),
        test_admin_token="dashboard-test-token",
    )


@pytest.fixture
def app(fake_config, supabase, redis):
    return _make_app(fake_config, supabase, redis)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seeded_customer(supabase, make_customer):
    make_customer(PSID_CUST, name="Runtime Test Customer")
    supabase.table("conversations").insert({
        "psid": PSID_CUST, "state": "collecting_preferences",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "last_activity_at": datetime.now(timezone.utc).isoformat(),
        "closed_at": None, "is_human_paused": False,
    })
    return PSID_CUST


# ---------------------------------------------------------------------------
# Denial paths
# ---------------------------------------------------------------------------


class TestLineAdminDenied:
    def test_missing_sender_denied(self, client):
        resp = client.post("/admin/line", json={"text": "cases"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is False
        assert body["action"] == "denied"
        assert body["error"] == "missing_sender"
        # The raw command MUST NOT be reflected.
        assert "cases" not in body["admin_text"]

    def test_non_allowlisted_sender_no_side_effects(self, client, supabase, seeded_customer):
        resp = client.post("/admin/line", json={
            "sender_id": DENIED,
            "text": f"pause {seeded_customer} attacker takeover",
        })
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is False
        assert body["error"] == "not_allowed"
        # No bot_pauses row inserted.
        assert is_bot_paused_for(supabase, seeded_customer) is False
        # No raw command echoed.
        assert "attacker takeover" not in body["admin_text"]

    def test_empty_allow_list_denies_known_sender(self, fake_config, supabase, redis):
        # Build a separate app with an empty allow-list.
        app = _make_app(fake_config, supabase, redis, allow_list=())
        client = app.test_client()
        resp = client.post("/admin/line", json={"sender_id": ALLOWED, "text": "cases"})
        body = resp.get_json()
        assert body["ok"] is False
        assert body["error"] == "empty_allow_list"

    def test_oversize_body_rejected(self, client):
        payload = json.dumps({
            "sender_id": ALLOWED,
            "text": "cases" + ("x" * 5000),
        }).encode()
        resp = client.post(
            "/admin/line", data=payload,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 413
        body = resp.get_json()
        assert body["error"] == "body_too_large"

    def test_invalid_json_returns_400(self, client):
        resp = client.post(
            "/admin/line", data=b"<<not json>>",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Allowed paths
# ---------------------------------------------------------------------------


class TestLineAdminAllowed:
    def test_authorized_cases_command(self, client, supabase, seeded_customer):
        resp = client.post("/admin/line", json={
            "sender_id": ALLOWED, "text": "cases",
        })
        body = resp.get_json()
        assert body["ok"] is True
        assert body["action"] == "cases"
        # No raw PSID in payload.
        assert PSID_CUST not in json.dumps(body)

    def test_authorized_pause_and_resume(self, client, supabase, seeded_customer):
        # Pause
        resp = client.post("/admin/line", json={
            "sender_id": ALLOWED,
            "text": f"pause {seeded_customer} admin chat",
        })
        body = resp.get_json()
        assert body["ok"] is True
        assert body["action"] == "pause"
        assert body["mutated"] is True
        assert is_bot_paused_for(supabase, seeded_customer) is True
        # Resume
        resp = client.post("/admin/line", json={
            "sender_id": ALLOWED,
            "text": f"resume {seeded_customer} done",
        })
        body = resp.get_json()
        assert body["ok"] is True
        assert body["action"] == "resume"
        assert is_bot_paused_for(supabase, seeded_customer) is False

    def test_authorized_mark_full_and_clear(self, client, supabase, make_tour):
        make_tour(web_code="ap777991", name="Runtime", price=21900)
        resp = client.post("/admin/line", json={
            "sender_id": ALLOWED, "text": "mark_full ap777991",
        })
        body = resp.get_json()
        assert body["ok"] is True
        assert body["mutated"] is True
        active = _active_overrides(supabase, web_code="ap777991")
        assert any(o.get("status") == "full" for o in active)
        # Clear
        resp = client.post("/admin/line", json={
            "sender_id": ALLOWED, "text": "clear_full ap777991",
        })
        body = resp.get_json()
        assert body["ok"] is True
        active = _active_overrides(supabase, web_code="ap777991")
        assert not any(o.get("status") == "full" for o in active)
