"""Sprint 2 test: Flask webhook receiver — unit-level, in-memory deps."""

import hashlib
import hmac
import json
import os
import time
import pytest

# Defer Flask import to skip-friendly check
flask_available = True
try:
    import flask  # noqa
except ImportError:
    flask_available = False

pytestmark = pytest.mark.skipif(not flask_available, reason="flask not installed")


@pytest.fixture
def fake_config():
    """Build a Config-like SimpleNamespace bypassing env strictness."""
    from types import SimpleNamespace
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


@pytest.fixture
def app(fake_config, supabase, redis):
    # Wire customers + conversations bootstrap helpers
    from v2.webhook.app import create_app
    return create_app(test_config=fake_config, test_supabase=supabase, test_redis=redis)


@pytest.fixture
def client(app):
    return app.test_client()


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _event(psid: str, text: str, mid: str = "m_test_1") -> dict:
    return {
        "object": "page",
        "entry": [{
            "messaging": [{
                "sender": {"id": psid},
                "recipient": {"id": "PAGE_ID"},
                "timestamp": 1736899200000,
                "message": {"mid": mid, "text": text},
            }]
        }]
    }


class TestVerification:
    def test_verify_handshake_success(self, client):
        resp = client.get("/webhook", query_string={
            "hub.mode": "subscribe",
            "hub.verify_token": "test-verify",
            "hub.challenge": "12345",
        })
        assert resp.status_code == 200
        assert resp.data == b"12345"

    def test_verify_handshake_wrong_token(self, client):
        resp = client.get("/webhook", query_string={
            "hub.mode": "subscribe",
            "hub.verify_token": "WRONG",
            "hub.challenge": "12345",
        })
        assert resp.status_code == 403


class TestHealthz:
    def test_healthz_ok(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"


class TestSignature:
    def test_reject_invalid_signature(self, client):
        body = json.dumps(_event("PSID1", "hi")).encode()
        resp = client.post("/webhook", data=body, headers={
            "X-Hub-Signature-256": "sha256=ffffffff",
            "Content-Type": "application/json",
        })
        assert resp.status_code == 401

    def test_accept_valid_signature(self, client):
        body = json.dumps(_event("PSID1", "hi")).encode()
        resp = client.post("/webhook", data=body, headers={
            "X-Hub-Signature-256": _sign(body, "test-app-secret"),
            "Content-Type": "application/json",
        })
        assert resp.status_code == 200


class TestProcessing:
    def test_ingests_event(self, client, supabase, redis):
        body = json.dumps(_event("PSID_NEW_1", "อยากไปญี่ปุ่น", mid="m_a1")).encode()
        resp = client.post("/webhook", data=body, headers={
            "X-Hub-Signature-256": _sign(body, "test-app-secret"),
            "Content-Type": "application/json",
        })
        assert resp.status_code == 200
        # Wait for background thread (best-effort; in-memory ops are fast)
        time.sleep(0.5)
        # Verify customer + conversation + turn rows created
        cust = supabase.table("customers").select_one({"psid": "PSID_NEW_1"})
        assert cust is not None
        conv = supabase.table("conversations").select_one({"psid": "PSID_NEW_1", "closed_at": None})
        assert conv is not None
        # Turn count == 1
        turns = supabase.table("conversation_turns").select_all({"psid": "PSID_NEW_1"})
        assert len(turns) == 1
        assert turns[0]["meta_message_id"] == "fb:m_a1"

    def test_duplicate_skipped(self, client, supabase, redis):
        body = json.dumps(_event("PSID_DUP_1", "hi", mid="m_dup")).encode()
        sig = _sign(body, "test-app-secret")
        headers = {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}
        client.post("/webhook", data=body, headers=headers)
        time.sleep(0.3)
        client.post("/webhook", data=body, headers=headers)
        time.sleep(0.3)
        client.post("/webhook", data=body, headers=headers)
        time.sleep(0.3)
        # Should only have processed once
        turns = supabase.table("conversation_turns").select_all({"psid": "PSID_DUP_1"})
        assert len(turns) == 1
