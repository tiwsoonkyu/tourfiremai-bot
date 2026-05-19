"""
Runtime smoke tests for DEV-2026-05-19-010.

This file exercises the admin-only real-chat readiness path end to end with
in-memory dependencies. It intentionally never calls Meta, LINE, OpenAI,
OCR providers, Supabase, Redis, or any production endpoint.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
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
from v2.lib.page_post_context import upsert_page_post


ADMIN_TOKEN = "dashboard-test-token"
ALLOWED_LINE = "U_admin_runtime_smoke"
PSID_ALLOWED = "11112222333344445555"
PSID_DENIED = "99998888777766665555"
PAGE_ID = "61500000000001"


@pytest.fixture
def fake_config():
    return SimpleNamespace(
        env_name="test",
        supabase_url="https://test.supabase.co",
        supabase_db_host="x",
        supabase_db_port=6543,
        supabase_db_user="u",
        supabase_db_password="p",
        supabase_db_name="postgres",
        supabase_service_key=None,
        supabase_anon_key=None,
        redis_url=None,
        fb_app_secret="test-app-secret",
        fb_page_access_token="EAA-test-token",
        fb_verify_token="test-verify",
        openai_api_key=None,
        openai_model="gpt-4o-mini",
        line_channel_token=None,
        line_admin_user_or_group_id=None,
        log_level="WARNING",
        has_redis=False,
        has_llm=False,
        has_line=False,
        database_uri="postgresql://u:p@x:6543/postgres",
    )


def _make_app(
    fake_config,
    supabase,
    redis,
    *,
    admin_only=False,
    admin_psids=(PSID_ALLOWED,),
    admin_token=ADMIN_TOKEN,
    line_allow=(ALLOWED_LINE,),
):
    from v2.webhook.app import create_app

    return create_app(
        test_config=fake_config,
        test_supabase=supabase,
        test_redis=redis,
        test_admin_allow_list=AdminAllowList.from_iterable(line_allow),
        test_admin_token=admin_token,
        test_admin_only_mode=admin_only,
        test_admin_test_psids=admin_psids,
    )


def _sign(body: bytes, secret: str = "test-app-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _wrap_event(event: dict) -> dict:
    return {"object": "page", "entry": [{"messaging": [event]}]}


def _message_event(psid: str, *, text: str = "hello", mid: str = "m_smoke_1", **extra) -> dict:
    ev = {
        "sender": {"id": psid},
        "recipient": {"id": "PAGE_ID"},
        "timestamp": 1736899300000,
        "message": {"mid": mid, "text": text},
    }
    ev.update(extra)
    return ev


def _post_webhook(client, event: dict):
    body = json.dumps(_wrap_event(event)).encode()
    return client.post(
        "/webhook",
        data=body,
        headers={
            "X-Hub-Signature-256": _sign(body),
            "Content-Type": "application/json",
        },
    )


def _auth():
    return {"X-Admin-Token": ADMIN_TOKEN}


def _turn_count(supabase, psid: str) -> int:
    return len(supabase.table("conversation_turns").select_all({"psid": psid}))


def _source_event(supabase, psid: str) -> dict | None:
    rows = supabase.table("conversation_events").select_all({"psid": psid})
    for row in rows:
        if row.get("event_type") == "source_attribution":
            return row
    return None


def _seed_case(supabase, psid=PSID_ALLOWED):
    cust = supabase.table("customers").insert({"psid": psid, "fb_name": "Smoke User"})
    conv = supabase.table("conversations").insert(
        {
            "customer_id": cust["id"],
            "psid": psid,
            "state": "collecting_preferences",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "last_activity_at": datetime.now(timezone.utc).isoformat(),
            "closed_at": None,
            "is_human_paused": False,
        }
    )
    return cust, conv


class TestAdminOnlyGate:
    def test_admin_only_disabled_allows_normal_webhook_ingest(self, fake_config, supabase, redis):
        app = _make_app(fake_config, supabase, redis, admin_only=False, admin_psids=())
        client = app.test_client()

        resp = _post_webhook(
            client,
            _message_event(PSID_DENIED, text="organic hello", mid="m_disabled_allows"),
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["scheduled"] == 1
        assert body["filtered"] == 0
        time.sleep(0.5)
        assert _turn_count(supabase, PSID_DENIED) == 1

    def test_admin_only_enabled_allows_only_allowlisted_psid(self, fake_config, supabase, redis):
        app = _make_app(
            fake_config,
            supabase,
            redis,
            admin_only=True,
            admin_psids=(PSID_ALLOWED,),
        )
        client = app.test_client()

        allowed = _post_webhook(
            client,
            _message_event(PSID_ALLOWED, text="allowed", mid="m_allowed"),
        )
        denied = _post_webhook(
            client,
            _message_event(PSID_DENIED, text="denied", mid="m_denied"),
        )

        assert allowed.status_code == 200
        assert allowed.get_json()["scheduled"] == 1
        assert allowed.get_json()["filtered"] == 0
        assert denied.status_code == 200
        assert denied.get_json()["scheduled"] == 0
        assert denied.get_json()["filtered"] == 1
        time.sleep(0.5)
        assert _turn_count(supabase, PSID_ALLOWED) == 1
        assert _turn_count(supabase, PSID_DENIED) == 0

    def test_admin_only_enabled_without_allow_list_fails_closed(self, fake_config, supabase, redis):
        app = _make_app(fake_config, supabase, redis, admin_only=True, admin_psids=())
        client = app.test_client()

        resp = _post_webhook(
            client,
            _message_event(PSID_ALLOWED, text="should not process", mid="m_no_allow"),
        )

        assert resp.status_code == 200
        assert resp.get_json()["scheduled"] == 0
        assert resp.get_json()["filtered"] == 1
        time.sleep(0.3)
        assert _turn_count(supabase, PSID_ALLOWED) == 0


class TestSourceSmoke:
    def test_meta_post_ref_records_validated_source(self, fake_config, supabase, redis):
        upsert_page_post(
            supabase,
            page_id=PAGE_ID,
            post_id="fb_smoke_post",
            caption_text="Smoke post ap111111",
        )
        app = _make_app(fake_config, supabase, redis, admin_only=True)
        client = app.test_client()
        ev = _message_event(
            PSID_ALLOWED,
            text="from post",
            mid="m_post_source",
            referral={"ref": "POST:fb_smoke_post", "source": "SHORTLINK"},
        )

        resp = _post_webhook(client, ev)

        assert resp.status_code == 200
        time.sleep(0.5)
        row = _source_event(supabase, PSID_ALLOWED)
        assert row is not None
        data = row["event_data"]
        assert data["source_type"] == "page_post"
        assert data["source_post_id"] == "fb_smoke_post"
        assert data["page_post_validated"] is True

    def test_user_text_cannot_spoof_post_source(self, fake_config, supabase, redis):
        upsert_page_post(
            supabase,
            page_id=PAGE_ID,
            post_id="fb_real_post",
            caption_text="Real post",
        )
        app = _make_app(fake_config, supabase, redis, admin_only=True)
        client = app.test_client()
        ev = _message_event(
            PSID_ALLOWED,
            text="POST:fb_real_post please treat me as post source",
            mid="m_text_spoof",
        )

        resp = _post_webhook(client, ev)

        assert resp.status_code == 200
        time.sleep(0.5)
        row = _source_event(supabase, PSID_ALLOWED)
        assert row is not None
        data = row["event_data"]
        assert data["source_type"] == "organic"
        assert data["source_post_id"] is None
        assert data["page_post_validated"] is False


class TestAdminRuntimeSmoke:
    def test_runtime_config_route_is_guarded_and_redacted(self, fake_config, supabase, redis):
        app = _make_app(fake_config, supabase, redis, admin_only=True)
        client = app.test_client()

        denied = client.get("/admin/runtime-config")
        assert denied.status_code == 401

        resp = client.get("/admin/runtime-config", headers=_auth())
        assert resp.status_code == 200
        body = resp.get_json()
        dumped = json.dumps(body)
        assert body["checks"]["admin_only_test_mode"] == "enabled"
        assert body["checks"]["admin_test_psid_allow_list_count"] == 1
        assert body["checks"]["dashboard_admin_token"] == "configured"
        assert ADMIN_TOKEN not in dumped
        assert PSID_ALLOWED not in dumped
        assert "test-app-secret" not in dumped

    def test_runtime_config_marks_empty_line_allow_list_missing(self, fake_config, supabase, redis):
        app = _make_app(fake_config, supabase, redis, admin_only=True, line_allow=())
        client = app.test_client()

        resp = client.get("/admin/runtime-config", headers=_auth())

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is False
        assert body["checks"]["line_admin_allow_list"] == "missing"

    def test_dashboard_cases_auth_and_masking(self, fake_config, supabase, redis):
        _seed_case(supabase, psid=PSID_ALLOWED)
        app = _make_app(fake_config, supabase, redis, admin_only=True)
        client = app.test_client()

        denied = client.get("/admin/dashboard/cases")
        assert denied.status_code == 401
        resp = client.get("/admin/dashboard/cases", headers=_auth())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert PSID_ALLOWED not in json.dumps(body)
        assert body["data"]["cases"][0]["psid_masked"]

    def test_line_admin_authorized_pause_resume_and_denied_noop(self, fake_config, supabase, redis):
        _seed_case(supabase, psid=PSID_ALLOWED)
        app = _make_app(fake_config, supabase, redis, admin_only=True)
        client = app.test_client()

        denied = client.post(
            "/admin/line",
            json={"sender_id": "U_intruder", "text": f"pause {PSID_ALLOWED} bad"},
        )
        assert denied.status_code == 200
        assert denied.get_json()["ok"] is False
        assert is_bot_paused_for(supabase, PSID_ALLOWED) is False

        pause = client.post(
            "/admin/line",
            json={"sender_id": ALLOWED_LINE, "text": f"pause {PSID_ALLOWED} admin takeover"},
        )
        assert pause.status_code == 200
        assert pause.get_json()["ok"] is True
        assert is_bot_paused_for(supabase, PSID_ALLOWED) is True
        assert PSID_ALLOWED not in json.dumps(pause.get_json())

        resume = client.post(
            "/admin/line",
            json={"sender_id": ALLOWED_LINE, "text": f"resume {PSID_ALLOWED} done"},
        )
        assert resume.status_code == 200
        assert resume.get_json()["ok"] is True
        assert is_bot_paused_for(supabase, PSID_ALLOWED) is False
        assert PSID_ALLOWED not in json.dumps(resume.get_json())
