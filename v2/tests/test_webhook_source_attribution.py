"""
v2/tests/test_webhook_source_attribution.py — DEV-2026-05-19-009.

Tests for the V2 webhook source-attribution seam:

    1. An event with a referral ref pointing at a known page_post records
       a `conversation_events` row with `event_type='source_attribution'`,
       `source_type='page_post'`, and the validated `source_post_id`.
    2. An event with no source signal records `source_type='organic'`
       (presence of message + sender qualifies) and a NULL
       `source_post_id`.
    3. An event with an unverified `source_post_id` (no matching
       page_posts row) records `source_type` ∈ {'organic', 'unknown'} and
       does NOT include the attacker-supplied id in `source_post_id`.
    4. A recorded source_attribution event can be replayed into the
       orchestrator and triggers the sold-out block via the runtime path.
    5. Webhook keeps silent-ingest behavior (no outbound reply written by
       the webhook background processor).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest

flask_available = True
try:
    import flask  # noqa: F401
except ImportError:
    flask_available = False

pytestmark = pytest.mark.skipif(not flask_available, reason="flask not installed")


from v2.lib import page_post_context as ppc
from v2.lib.llm import MockLLMClient
from v2.lib.orchestrator import Orchestrator
from v2.lib.page_post_context import (
    mark_availability_override,
    upsert_page_post,
)


PAGE_ID = "61500000000001"
ADMIN_ID = "line-admin-runtime"


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


@pytest.fixture
def app(fake_config, supabase, redis):
    from v2.webhook.app import create_app
    return create_app(
        test_config=fake_config, test_supabase=supabase, test_redis=redis,
        test_admin_token="dashboard-test-token",
    )


@pytest.fixture
def client(app):
    return app.test_client()


def _sign(body: bytes, secret: str = "test-app-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _wrap_event(messaging_event: dict) -> dict:
    return {"object": "page", "entry": [{"messaging": [messaging_event]}]}


def _post_webhook(client, event: dict):
    body = json.dumps(_wrap_event(event)).encode()
    resp = client.post("/webhook", data=body, headers={
        "X-Hub-Signature-256": _sign(body),
        "Content-Type": "application/json",
    })
    return resp


def _find_source_attribution(supabase, psid: str) -> dict | None:
    rows = supabase.table("conversation_events").select_all({"psid": psid})
    for r in rows:
        if r.get("event_type") == "source_attribution":
            return r
    return None


class TestWebhookSourceAttribution:
    def test_page_post_event_records_validated_attribution(self, client, supabase, redis):
        upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="fb_runtime_known",
            caption_text="ทัวร์ไฟไหม้ ap908100",
        )
        psid = "PSID_RUNTIME_PP"
        event = {
            "sender": {"id": psid},
            "recipient": {"id": "PAGE_ID"},
            "timestamp": 1736899300000,
            "message": {"mid": "m_rt_1", "text": "สนใจโพสต์นี้"},
            "referral": {"ref": "POST:fb_runtime_known", "source": "SHORTLINK"},
        }
        resp = _post_webhook(client, event)
        assert resp.status_code == 200
        # Background thread completes
        time.sleep(0.5)

        attribution = _find_source_attribution(supabase, psid)
        assert attribution is not None
        data = attribution["event_data"]
        assert data["source_type"] == "page_post"
        assert data["source_post_id"] == "fb_runtime_known"
        assert data["page_post_validated"] is True
        assert data["page_post_id"]
        assert attribution["triggered_by"] == "system"
        # meta_message_id MUST be None so the unique (platform, meta_message_id)
        # index does not collide with the state_change row.
        assert attribution["meta_message_id"] is None

    def test_organic_event_records_unknown_or_organic(self, client, supabase, redis):
        psid = "PSID_RUNTIME_ORG"
        event = {
            "sender": {"id": psid},
            "recipient": {"id": "PAGE_ID"},
            "timestamp": 1736899301000,
            "message": {"mid": "m_rt_org", "text": "อยากไปเที่ยวค่ะ"},
        }
        resp = _post_webhook(client, event)
        assert resp.status_code == 200
        time.sleep(0.5)

        attribution = _find_source_attribution(supabase, psid)
        assert attribution is not None
        data = attribution["event_data"]
        assert data["source_type"] == "organic"
        assert data["source_post_id"] is None
        assert data["page_post_validated"] is False

    def test_unverified_post_id_is_not_recorded_as_validated(self, client, supabase, redis):
        psid = "PSID_RUNTIME_FAKE"
        event = {
            "sender": {"id": psid},
            "recipient": {"id": "PAGE_ID"},
            "timestamp": 1736899302000,
            "message": {"mid": "m_rt_fake", "text": "ตอบโพสต์"},
            "referral": {"ref": "POST:no_such_post_in_db"},
        }
        resp = _post_webhook(client, event)
        assert resp.status_code == 200
        time.sleep(0.5)

        attribution = _find_source_attribution(supabase, psid)
        assert attribution is not None
        data = attribution["event_data"]
        # Either organic (presence of message+referral) or unknown — never
        # page_post, since DB lookup must fail.
        assert data["source_type"] in {"organic", "unknown"}
        assert data["page_post_validated"] is False
        # Attacker-supplied id must NOT be threaded through.
        assert data["source_post_id"] is None

    def test_webhook_is_silent_no_outbound_reply(self, client, supabase, redis):
        """Sprint 5 Package C must NOT enable customer-facing replies. The
        webhook background processor still only persists inbound turns."""
        psid = "PSID_RUNTIME_SILENT"
        event = {
            "sender": {"id": psid},
            "recipient": {"id": "PAGE_ID"},
            "timestamp": 1736899303000,
            "message": {"mid": "m_rt_silent", "text": "สวัสดีค่ะ"},
        }
        _post_webhook(client, event)
        time.sleep(0.5)
        turns = supabase.table("conversation_turns").select_all({"psid": psid})
        assert len(turns) == 1
        # The only turn is inbound; no outbound turn was written.
        assert turns[0]["direction"] == "inbound"


class TestRuntimeReachesPlanning:
    """The recorded source_attribution event is the seam future code uses
    to invoke the orchestrator. This test proves the wire reaches planning
    by manually replaying the recorded attribution into the orchestrator."""

    def test_recorded_attribution_blocks_via_orchestrator(self, client, supabase, redis, make_tour):
        make_tour(
            web_code="ap908200", name="ทัวร์โพสต์เต็ม",
            price=22000, country="ญี่ปุ่น", country_id=2,
        )
        post = upsert_page_post(
            supabase, page_id=PAGE_ID, post_id="fb_runtime_full",
            caption_text="ap908200",
        )
        mark_availability_override(
            supabase, scope="post", status="full",
            page_post_id=post.id, marked_by=ADMIN_ID,
        )

        psid = "PSID_RUNTIME_BLOCK"
        # 1. Webhook ingests the event and records the source attribution.
        event = {
            "sender": {"id": psid},
            "recipient": {"id": "PAGE_ID"},
            "timestamp": 1736899304000,
            "message": {"mid": "m_rt_block", "text": "ตัวเลือกนี้ค่ะ"},
            "referral": {"ref": "POST:fb_runtime_full", "source": "SHORTLINK"},
        }
        _post_webhook(client, event)
        time.sleep(0.5)

        # 2. Pre-seed a selected tour so the orchestrator's planner can
        # resolve the candidate web_code.
        tour_row = supabase.table("tours_canonical").select_one(
            {"web_code": "ap908200"}
        )
        cust = supabase.table("customers").select_one({"psid": psid})
        conv = supabase.table("conversations").select_one(
            {"psid": psid, "closed_at": None}
        )
        # Update conversation state to tour_selected so planner inspects it.
        supabase.table("conversations").update(
            {"id": conv["id"]}, {"state": "tour_selected"}
        )
        supabase.table("selected_tours").insert({
            "conversation_id": conv["id"], "customer_id": cust["id"],
            "psid": psid, "tour_id": tour_row["id"],
        })

        # 3. Read the recorded source attribution back and forward into the
        # orchestrator. This is the seam future code will use.
        attribution = _find_source_attribution(supabase, psid)
        data = attribution["event_data"]
        kwargs = {
            "source_type": data["source_type"],
            "source_post_id": data["source_post_id"],
            "source_platform": data["source_platform"],
        }

        orch = Orchestrator(supabase, redis, MockLLMClient())
        result = orch.handle_turn(
            psid=psid, text="ตัวเลือกในโพสต์", meta_message_id="fb:rt_replay_1",
            **kwargs,
        )
        assert result.decision == "canned_blocked"
        assert result.reply_text == ppc.REASON_POST_FULL
