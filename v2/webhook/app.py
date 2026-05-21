"""
v2.webhook.app — Flask webhook receiver for Meta Messenger.

Sprint 2 deliverable. Wires:
  - X-Hub-Signature-256 verification
  - Idempotency check (Redis NX) + DB-level meta_message_id unique index
  - Per-PSID conversation lock (60s TTL)
  - Inbound message persistence (conversation_turns)
  - DLQ promotion on repeated failure (>3)
  - Webhook acked < 5s; processing deferred to background thread

In Sprint 2 there is NO LLM call and NO outbound message — the bot remains
silent. We just ingest, persist, log a state-machine decision, and return 200.

Endpoints:
    GET  /webhook          — Meta verification handshake
    POST /webhook          — Meta event delivery
    GET  /healthz          — liveness probe
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Optional

from flask import Flask, request, jsonify

from ..lib import config as cfg_module
from ..lib import idempotency, redactor
from ..lib.cache import make_redis
from ..lib.db import make_supabase_from_config
from ..lib.intent import classify
from ..lib.llm import make_llm_client
from ..lib.meta_sender import MetaMessengerSender
from ..lib.orchestrator import Orchestrator
from ..lib.source_attribution import extract_source
from .test_mode_gate import admin_only_enabled, admin_test_psids, should_process_inbound
from ..lib.state_machine import (
    State, StateContext, transition, allowed_tools, is_silent_state,
)

logger = logging.getLogger("v2.webhook")


# --- App factory --------------------------------------------------------------

def create_app(*, test_config=None, test_supabase=None, test_redis=None,
                test_admin_allow_list=None, test_admin_token=None,
                test_memory=None, test_admin_only_mode=None,
                test_admin_test_psids=None, test_meta_sender=None,
                test_llm=None, test_http_client=None,
                test_admin_outbound_enabled=None) -> Flask:
    """
    Flask app factory. test_config + injected dependencies for unit tests.

    test_admin_allow_list / test_admin_token are Sprint 5 Package C hooks
    so the admin runtime routes can be exercised under tests without
    setting environment variables. In production these read from
    `V2_STAGING_LINE_ADMIN_ALLOW_LIST` and `V2_STAGING_DASHBOARD_TOKEN`
    via the helper functions in `v2.webhook.admin_routes`.

    test_admin_only_mode / test_admin_test_psids are Sprint 5 Package D
    hooks for admin-only real-chat readiness. They are intentionally
    config-only and never enable outbound customer replies.
    """
    app = Flask(__name__)
    app.url_map.strict_slashes = False

    # Config
    if test_config is not None:
        config = test_config
    else:
        config = cfg_module.load_config(strict=True)

    # Dependencies
    redis = test_redis if test_redis is not None else make_redis(config)
    supabase = test_supabase if test_supabase is not None else make_supabase_from_config(config)

    # Stash on app for handlers + tests
    app.config["V2_CONFIG"] = config
    app.config["V2_REDIS"] = redis
    app.config["V2_SUPABASE"] = supabase
    app.config["V2_MEMORY"] = test_memory
    app.config["V2_ADMIN_ALLOW_LIST"] = test_admin_allow_list
    app.config["V2_ADMIN_TOKEN"] = test_admin_token
    app.config["V2_LLM_CLIENT"] = test_llm
    app.config["V2_HTTP_CLIENT"] = test_http_client
    app.config["V2_META_SENDER"] = (
        test_meta_sender if test_meta_sender is not None
        else MetaMessengerSender(getattr(config, "fb_page_access_token", None))
    )
    app.config["V2_ADMIN_OUTBOUND_ENABLED"] = test_admin_outbound_enabled
    if test_admin_only_mode is not None:
        app.config["V2_ADMIN_ONLY_TEST_MODE"] = test_admin_only_mode
    if test_admin_test_psids is not None:
        app.config["V2_ADMIN_TEST_PSID_ALLOW_LIST"] = test_admin_test_psids

    _register_routes(app)

    # Sprint 5 Package C admin runtime — lazy import so the webhook module
    # remains usable without the admin routes for callers that only want
    # the Meta webhook surface.
    from .admin_routes import register_admin_routes
    register_admin_routes(app)

    return app


# --- Helpers ------------------------------------------------------------------

def _verify_meta_signature(body_bytes: bytes, signature_header: str, app_secret: str) -> bool:
    """Verify X-Hub-Signature-256 sent by Meta."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = signature_header.split("=", 1)[1]
    computed = hmac.new(
        app_secret.encode("utf-8"), body_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(computed, expected)


def _redacted_event_for_log(event: dict) -> dict:
    return redactor.redact_event(event)


def _should_ignore_meta_event(event: dict) -> tuple[bool, str]:
    """
    Filter Meta events that are not customer-originated messages.

    Messenger can send message echoes for messages our Page sends. Processing
    those echoes as inbound messages creates a reply loop, so this guard runs
    before admin-only allow-listing, idempotency, or background scheduling.
    """
    msg = event.get("message")
    if not isinstance(msg, dict):
        return True, "non_message_event"
    if msg.get("is_echo") is True:
        return True, "message_echo"
    if not (msg.get("text") or msg.get("attachments") or msg.get("quick_reply")):
        return True, "empty_message"
    return False, ""


def _truthy_env(name: str, *, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _admin_outbound_enabled(app: Flask, psid: str) -> bool:
    """Allow outbound only during admin-only testing and only to allowlisted PSIDs."""
    explicit = app.config.get("V2_ADMIN_OUTBOUND_ENABLED")
    if explicit is None:
        explicit = _truthy_env("V2_STAGING_ADMIN_OUTBOUND_ENABLED", default=False)
    if not explicit:
        return False
    if not admin_only_enabled(config=app.config):
        return False
    allowed = set(admin_test_psids(config=app.config))
    if not psid or str(psid) not in allowed:
        return False
    return True


def _get_llm_client(app: Flask):
    client = app.config.get("V2_LLM_CLIENT")
    if client is None:
        client = make_llm_client(app.config["V2_CONFIG"])
        app.config["V2_LLM_CLIENT"] = client
    return client


def _process_event_admin_outbound(app: Flask, event: dict, full_message_id: str,
                                  trace_id: str) -> None:
    """Run the V2 orchestrator and send one Messenger reply for admin-only tests."""
    supabase = app.config["V2_SUPABASE"]
    redis_ = app.config["V2_REDIS"]
    sender = event.get("sender") or {}
    psid = sender.get("id")
    msg = event.get("message") or {}
    text = msg.get("text") or ""
    attachments = msg.get("attachments") or []

    attr_kwargs: dict[str, Any] = {}
    try:
        attr = extract_source(event, supabase)
        if attr:
            attr_kwargs = attr.to_orchestrator_kwargs()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("[%s] source attribution skipped: %s", trace_id, e)

    orchestrator = Orchestrator(
        supabase,
        redis_,
        _get_llm_client(app),
        http_client=app.config.get("V2_HTTP_CLIENT"),
    )
    result = orchestrator.handle_turn(
        psid=psid,
        text=text,
        attachments=attachments,
        meta_message_id=full_message_id,
        platform="fb",
        trace_id=trace_id,
        **attr_kwargs,
    )
    if not result.reply_text:
        logger.info("[%s] admin-only orchestrator returned silent decision=%s", trace_id, result.decision)
        return

    meta_sender = app.config.get("V2_META_SENDER")
    if meta_sender is None:
        logger.error("[%s] Meta sender not configured", trace_id)
        return
    send_result = meta_sender.send_text(psid, result.reply_text)
    if send_result.ok:
        logger.info("[%s] admin-only Messenger reply sent status=%s", trace_id, send_result.status_code)
    else:
        logger.error(
            "[%s] admin-only Messenger reply failed status=%s error=%s",
            trace_id, send_result.status_code, send_result.error,
        )


# --- Background processing ----------------------------------------------------

def _persist_inbound(supabase, *, conversation_id: str, psid: str, turn_number: int,
                    text: str, meta_message_id: str, intent_dict: dict, state_before: str,
                    state_after: str) -> Optional[dict]:
    """Insert inbound conversation_turns row. Returns row or None on dedup."""
    try:
        return supabase.table("conversation_turns").insert({
            "id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "psid": psid,
            "turn_number": turn_number,
            "direction": "inbound",
            "speaker": "customer",
            "message_text": text,
            "intent": intent_dict,
            "state_before": state_before,
            "state_after": state_after,
            "meta_message_id": meta_message_id,
            "platform": "fb",
        })
    except Exception as e:
        # Likely the dedup unique index — that's the desired behavior on replay
        if "idx_turns_dedup" in str(e):
            logger.info("Dedup hit on conversation_turns for %s", meta_message_id)
            return None
        raise


def _get_or_create_conversation(supabase, psid: str) -> dict:
    """Idempotently fetch active conversation row; create with state=new_lead if missing."""
    existing = supabase.table("conversations").select_one({"psid": psid, "closed_at": None})
    if existing:
        return existing

    # Need customer first
    customer = supabase.table("customers").select_one({"psid": psid})
    if not customer:
        customer = supabase.table("customers").insert({"psid": psid})

    row = supabase.table("conversations").insert({
        "customer_id": customer["id"],
        "psid": psid,
        "state": State.NEW_LEAD.value,
    })
    return row


def _increment_turn_counter(supabase, conversation_id: str) -> int:
    """
    Return next turn_number atomically. Locks conversations row to prevent
    races between concurrent webhook threads on the same PSID.

    Pattern: BEGIN; SELECT 1 FROM conversations WHERE id=... FOR UPDATE;
             SELECT COALESCE(MAX(turn_number),0)+1 FROM conversation_turns ...; COMMIT.
    The per-PSID Redis lock SHOULD already serialize this, but the DB lock
    is defense-in-depth in case Redis is bypassed or evicted mid-flight.
    """
    with supabase.table("conversation_turns")._cursor() as cur:
        # Lock the conversations row
        cur.execute(
            'SELECT id FROM "conversations" WHERE id = %s FOR UPDATE',
            [conversation_id],
        )
        cur.execute(
            'SELECT COALESCE(MAX(turn_number), 0) FROM "conversation_turns" WHERE "conversation_id" = %s',
            [conversation_id],
        )
        max_n = cur.fetchone()[0] or 0
    return int(max_n) + 1


def _record_source_attribution(supabase, *, conversation_id, psid, event,
                                trace_id):
    """
    Record a deterministic source-attribution row on `conversation_events`
    so a future orchestrator caller can read back what source this turn
    came from. Silent-ingest by design — no outbound reply is produced
    here. Returns the SourceAttribution that was recorded (or None on
    parse failure / lookup error).

    The row stores:
        event_type      = "source_attribution"
        event_data      = {source_type, source_post_id, source_platform,
                           page_post_id, page_post_validated, raw_ref}
        triggered_by    = "system"
        meta_message_id = None     (avoid the (platform, meta_message_id)
                                    unique index — the state_change row
                                    already owns that key)
    """
    try:
        attr = extract_source(event, supabase)
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("[%s] source_attribution extract failed: %s", trace_id, e)
        return None
    try:
        supabase.table("conversation_events").insert({
            "id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "psid": psid,
            "event_type": "source_attribution",
            "event_data": {
                "source_type": attr.source_type,
                "source_post_id": (
                    attr.source_post_id if attr.page_post_validated else None
                ),
                "source_platform": attr.source_platform,
                "page_post_id": attr.page_post_id,
                "page_post_validated": bool(attr.page_post_validated),
                "raw_ref": attr.raw_ref,
            },
            "triggered_by": "system",
            "meta_message_id": None,
            "platform": (
                "line" if attr.source_platform == "line"
                else ("fb" if attr.source_platform == "facebook" else "fb")
            ),
        })
    except Exception as e:
        logger.warning("[%s] source_attribution persist failed: %s", trace_id, e)
    return attr


def _process_event(app, event: dict, full_message_id: str, trace_id: str) -> None:
    """Background work after webhook ack. Persists turn + computes state transition."""
    supabase = app.config["V2_SUPABASE"]
    redis_ = app.config["V2_REDIS"]

    sender = event.get("sender") or {}
    psid = sender.get("id")
    msg = event.get("message") or {}
    text = msg.get("text") or ""
    attachments = msg.get("attachments") or []

    if not psid:
        logger.warning("[%s] missing PSID in event — DLQ candidate", trace_id)
        return

    # Acquire per-PSID lock
    lock = idempotency.ConversationLock(redis_, psid, trace_id)
    if not lock.acquire():
        # Track contention so it's visible in DLQ retry counter (not a hard failure yet)
        retry_key = f"retry:{full_message_id}"
        try:
            current = redis_.get(retry_key)
            count = int(current) if current and current.isdigit() else 0
            redis_.set(retry_key, str(count + 1), ex=86400)
        except Exception:
            pass
        logger.warning(
            "[%s] lock-timeout for PSID %s (retry %d) — Meta will retry",
            trace_id, redactor.mask_psid(psid), count + 1 if 'count' in locals() else 1,
        )
        # Force clear idem to allow retry
        idempotency.DuplicateChecker(redis_).force_clear(full_message_id)
        return

    try:
        if _admin_outbound_enabled(app, psid):
            _process_event_admin_outbound(app, event, full_message_id, trace_id)
            return

        # Conversation row
        conv = _get_or_create_conversation(supabase, psid)
        state_before = State(conv["state"])

        # Classify intent (rule-based only in Sprint 2)
        intent_obj = classify(text, attachments=attachments, current_state=state_before.value)

        # Transition
        ctx = StateContext()
        result = transition(state_before, _intent_to_sm(intent_obj), ctx)
        state_after = result.next_state

        # Persist inbound turn (dedup-safe)
        turn_no = _increment_turn_counter(supabase, conv["id"])
        _persist_inbound(
            supabase,
            conversation_id=conv["id"],
            psid=psid,
            turn_number=turn_no,
            text=text,
            meta_message_id=full_message_id,
            intent_dict=intent_obj.to_dict(),
            state_before=state_before.value,
            state_after=state_after.value,
        )

        # Update conversation state if changed
        if state_before != state_after:
            supabase.table("conversations").update(
                {"id": conv["id"]},
                {"state": state_after.value, "last_activity_at": "now()"},
            )
            supabase.table("conversation_events").insert({
                "id": str(uuid.uuid4()),
                "conversation_id": conv["id"],
                "psid": psid,
                "event_type": "state_change",
                "event_data": {"from": state_before.value, "to": state_after.value, "reason": result.reason},
                "triggered_by": "bot",
                "meta_message_id": full_message_id,
                "platform": "fb",
            })

        # Sprint 5 Package C — source attribution seam (silent-ingest).
        # Records what source this turn came from so a future orchestrator
        # caller can read it back from `conversation_events`. Never raises.
        attr = _record_source_attribution(
            supabase, conversation_id=conv["id"], psid=psid,
            event=event, trace_id=trace_id,
        )

        logger.info(
            "[%s] PSID=%s state=%s→%s intent=%s reason=%s tools=%s src=%s post=%s",
            trace_id, redactor.mask_psid(psid),
            state_before.value, state_after.value,
            intent_obj.type, result.reason, result.tool_hints,
            (attr.source_type if attr else "unknown"),
            (attr.source_post_id if attr and attr.page_post_validated else None),
        )

    except Exception as e:
        logger.exception("[%s] processing failed: %s", trace_id, e)
        _maybe_promote_dlq(supabase, redis_, full_message_id, psid, event, str(e))
    finally:
        lock.release()


def _intent_to_sm(intent_obj):
    """Adapt v2.lib.intent.Intent → v2.lib.state_machine.Intent."""
    from .. import lib  # noqa
    from ..lib.state_machine import Intent as SMIntent
    return SMIntent(
        type=intent_obj.type,
        raw_text=intent_obj.raw_text,
        country=intent_obj.country,
        budget=intent_obj.budget,
        selected_index=intent_obj.selected_index,
        selected_code=intent_obj.selected_code,
        has_attachment=intent_obj.has_attachment,
    )


def _maybe_promote_dlq(supabase, redis_, full_message_id: str, psid: str,
                       event: dict, error: str) -> None:
    """Increment retry counter; if > 3, write to dlq_messages."""
    retry_key = f"retry:{full_message_id}"
    current = redis_.get(retry_key)
    count = int(current) if current and current.isdigit() else 0
    count += 1
    redis_.set(retry_key, str(count), ex=86400)

    if count > 3:
        logger.error("DLQ promotion for %s after %d failures", full_message_id, count)
        try:
            supabase.table("dlq_messages").insert({
                "id": str(uuid.uuid4()),
                "platform": "fb",
                "meta_message_id": full_message_id,
                "psid": psid,
                "raw_payload": redactor.redact_event(event),
                "failure_count": count,
                "last_error": error[:1000],
                "first_failed_at": "now()",
                "last_failed_at": "now()",
            })
        except Exception as dlq_err:
            logger.error("DLQ write itself failed: %s", dlq_err)


# --- Routes -------------------------------------------------------------------

def _register_routes(app: Flask) -> None:
    config = app.config["V2_CONFIG"]

    @app.route("/healthz", methods=["GET"])
    def healthz():
        return jsonify({
            "status": "ok",
            "env": config.env_name,
            "build_commit": (
                os.getenv("RAILWAY_GIT_COMMIT_SHA")
                or os.getenv("RAILWAY_GIT_COMMIT")
                or os.getenv("GIT_COMMIT")
            ),
            "runtime_marker": "v2-admin-outbound-20260521",
            "has_redis": config.has_redis,
            "has_llm": config.has_llm,
            "has_line": config.has_line,
        })

    @app.route("/webhook", methods=["GET"])
    def verify():
        """Meta webhook verification handshake."""
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == config.fb_verify_token:
            return challenge or "", 200
        return "forbidden", 403

    @app.route("/webhook", methods=["POST"])
    def receive():
        raw = request.get_data()
        sig = request.headers.get("X-Hub-Signature-256", "")

        # 1) Verify signature
        if not config.fb_app_secret:
            logger.error("FB_APP_SECRET not configured — refusing all webhooks")
            return "misconfigured", 500
        if not _verify_meta_signature(raw, sig, config.fb_app_secret):
            return "invalid signature", 401

        # 2) Parse body
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            return "invalid json", 400

        if body.get("object") != "page":
            return "ignored", 200

        # 3) For each entry → each messaging event
        redis_ = app.config["V2_REDIS"]
        scheduled = 0
        filtered = 0

        for entry in body.get("entry", []):
            for ev in entry.get("messaging", []):
                ignored, ignore_reason = _should_ignore_meta_event(ev)
                if ignored:
                    filtered += 1
                    logger.info("Meta event ignored before processing reason=%s", ignore_reason)
                    continue

                psid = ((ev.get("sender") or {}).get("id"))
                gate = should_process_inbound(psid, config=app.config)
                if not gate.allowed:
                    filtered += 1
                    logger.info(
                        "admin-only gate filtered inbound psid=%s reason=%s",
                        gate.psid_masked, gate.reason,
                    )
                    continue

                identity = idempotency.build_meta_message_id(ev, platform="fb")
                dup_result = idempotency.check_duplicate_event(redis_, identity.full_id)
                if dup_result.is_duplicate:
                    logger.info("Duplicate %s (trace_id=%s)", identity.full_id, dup_result.trace_id)
                    continue

                # Defer to background thread; Flask returns 200 immediately
                thread = threading.Thread(
                    target=_process_event,
                    args=(app, ev, identity.full_id, dup_result.trace_id),
                    name=f"v2-proc-{dup_result.trace_id[:8]}",
                    daemon=True,
                )
                thread.start()
                scheduled += 1

        return jsonify({
            "status": "accepted",
            "scheduled": scheduled,
            "filtered": filtered,
        }), 200

    @app.errorhandler(Exception)
    def _on_error(e):
        logger.exception("Unhandled error: %s", e)
        return "internal error", 500


# --- Entrypoint ---------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("V2_STAGING_LOG_LEVEL", "INFO"))
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
