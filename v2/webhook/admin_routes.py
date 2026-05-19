"""
v2.webhook.admin_routes — DEV-2026-05-19-009.

Minimal Flask runtime shim around the Sprint 5 Package B adapters:

    * POST /admin/line             — LINE admin command entrypoint
    * GET  /admin/dashboard/cases             — list current cases
    * GET  /admin/dashboard/cases/<id>        — single case by psid OR conv id
    * GET  /admin/dashboard/posts             — recent page posts
    * GET  /admin/dashboard/handoffs          — open handoffs
    * GET  /admin/healthz                     — admin surface liveness

All routes are read-only, never call live LINE / Meta / OpenAI APIs, and
never expose raw PSIDs, captions, secrets, or wholesale brand names.

Authentication:

    * LINE entrypoint  — gated by `LineAdminAdapter` allow-list (sender id
      taken from JSON body `sender_id`). The adapter denies non-allowed
      senders before any side effect.
    * Dashboard reads  — gated by an `X-Admin-Token` header compared in
      constant time against `app.config["V2_ADMIN_TOKEN"]`. The token is
      injected at test time and read from `V2_STAGING_DASHBOARD_TOKEN` in
      production via `_admin_token_from_env`.

The dashboard auth is intentionally simple (single static token). A
later sprint can swap to OAuth / Supabase RLS without changing the
service contract this shim exposes.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Optional

from flask import Flask, jsonify, request

from ..lib.admin_dashboard_api import AdminContext, AdminDashboardAPI
from ..lib.line_admin_adapter import AdminAllowList, LineAdminAdapter
from .test_mode_gate import runtime_config_status

logger = logging.getLogger("v2.webhook.admin_routes")

# Env names for runtime configuration. NEVER include the actual token /
# allow-list contents in error messages or logs.
_ENV_DASHBOARD_TOKEN = "V2_STAGING_DASHBOARD_TOKEN"

# Hard cap on body size for the LINE entrypoint to refuse oversize payloads.
_MAX_LINE_BODY_BYTES = 4096


def _admin_token_from_config(app: Flask) -> Optional[str]:
    """Read the dashboard admin token from app config, then env, in that order."""
    tok = app.config.get("V2_ADMIN_TOKEN")
    if tok:
        return str(tok)
    return os.environ.get(_ENV_DASHBOARD_TOKEN)


def _allow_list_from_config(app: Flask) -> AdminAllowList:
    """
    Build the LINE admin allow-list. Test injection wins; otherwise read
    from V2_STAGING env via `AdminAllowList.from_env`.
    """
    injected = app.config.get("V2_ADMIN_ALLOW_LIST")
    if isinstance(injected, AdminAllowList):
        return injected
    if isinstance(injected, (list, tuple, set, frozenset)):
        return AdminAllowList.from_iterable(injected)
    return AdminAllowList.from_env()


def _denied_response(reason: str, status: int = 401):
    return jsonify({
        "ok": False, "action": "denied", "error": reason, "data": None,
    }), status


def _strip_raw_psid(obj):
    """
    Walk a JSON-serialisable structure and remove any top-level `psid`
    keys. Masked variants (`psid_masked`) are preserved. This is the
    safety net that ensures even if an upstream view-model accidentally
    includes the raw PSID in its `to_dict()`, the runtime HTTP layer
    refuses to ship it over the wire.
    """
    if isinstance(obj, dict):
        return {
            k: _strip_raw_psid(v)
            for k, v in obj.items()
            if k != "psid"
        }
    if isinstance(obj, list):
        return [_strip_raw_psid(v) for v in obj]
    if isinstance(obj, tuple):
        return [_strip_raw_psid(v) for v in obj]
    return obj


def _check_admin_token(app: Flask) -> Optional[tuple]:
    """
    Validate X-Admin-Token in constant time. Returns None on success or
    a (Flask response, status) tuple on failure.
    """
    expected = _admin_token_from_config(app)
    if not expected:
        return _denied_response("admin_token_not_configured", 500)
    presented = request.headers.get("X-Admin-Token", "")
    if not presented:
        return _denied_response("missing_admin_token", 401)
    if not isinstance(presented, str):
        return _denied_response("invalid_admin_token", 401)
    if not hmac.compare_digest(presented, expected):
        return _denied_response("invalid_admin_token", 401)
    return None


def _build_admin_context(app: Flask) -> AdminContext:
    """Build an AdminContext for the dashboard layer."""
    return AdminContext(
        admin_user_id="dashboard-token-bearer",
        allowed=True,
        source="web",
    )


def register_admin_routes(app: Flask) -> None:
    """Register Sprint 5 Package C admin routes on the given Flask app."""

    @app.route("/admin/healthz", methods=["GET"])
    def admin_healthz():
        allow = _allow_list_from_config(app)
        has_token = bool(_admin_token_from_config(app))
        return jsonify({
            "status": "ok",
            "admin_allow_list_count": len(allow.ids),
            "admin_dashboard_token_configured": has_token,
        })

    @app.route("/admin/runtime-config", methods=["GET"])
    def admin_runtime_config():
        denied = _check_admin_token(app)
        if denied:
            return denied
        payload = runtime_config_status(
            app_config=app.config,
            runtime_config=app.config.get("V2_CONFIG"),
        )
        return jsonify(_strip_raw_psid(payload)), 200

    # -----------------------------------------------------------------
    # LINE admin entrypoint
    # -----------------------------------------------------------------

    @app.route("/admin/line", methods=["POST"])
    def line_admin():
        """
        Accept a JSON body `{sender_id, text}` and dispatch through the
        allow-list-gated `LineAdminAdapter`. Returns the
        `AdminCommandResult` as JSON (including `admin_text`). Never
        calls the LINE Messaging API.
        """
        raw = request.get_data(cache=True, as_text=False) or b""
        if len(raw) > _MAX_LINE_BODY_BYTES:
            return _denied_response("body_too_large", 413)
        if not raw:
            return _denied_response("invalid_json", 400)
        try:
            body = request.get_json(silent=True, force=True) or {}
        except Exception:
            return _denied_response("invalid_json", 400)
        if not isinstance(body, dict) or not body:
            return _denied_response("invalid_json", 400)

        sender_id = body.get("sender_id")
        text = body.get("text") or ""
        if not isinstance(text, str):
            return _denied_response("invalid_text", 400)

        supabase = app.config["V2_SUPABASE"]
        memory = app.config.get("V2_MEMORY")
        allow = _allow_list_from_config(app)
        adapter = LineAdminAdapter(
            supabase=supabase, allow_list=allow, memory=memory,
        )
        result = adapter.handle(sender_id=sender_id, text=text)
        # HTTP status: 200 for all admin-visible results; 401 only on a
        # *transport-level* denial (which the adapter never produces — it
        # returns `ok=False` with a Thai admin_text on denial so the LINE
        # caller can echo a polite message).
        payload = _strip_raw_psid(result.to_dict())
        return jsonify(payload), 200

    # -----------------------------------------------------------------
    # Dashboard read shim
    # -----------------------------------------------------------------

    @app.route("/admin/dashboard/cases", methods=["GET"])
    def dashboard_list_cases():
        denied = _check_admin_token(app)
        if denied:
            return denied
        api = AdminDashboardAPI(
            supabase=app.config["V2_SUPABASE"],
            memory=app.config.get("V2_MEMORY"),
        )
        only_paused_arg = (request.args.get("only_paused") or "").lower()
        only_paused = only_paused_arg in ("1", "true", "yes")
        try:
            limit = int(request.args.get("limit") or 20)
        except (TypeError, ValueError):
            limit = 20
        payload = api.list_cases(
            _build_admin_context(app), limit=limit, only_paused=only_paused,
        )
        return jsonify(_strip_raw_psid(payload)), 200

    @app.route("/admin/dashboard/cases/<identifier>", methods=["GET"])
    def dashboard_get_case(identifier: str):
        denied = _check_admin_token(app)
        if denied:
            return denied
        api = AdminDashboardAPI(
            supabase=app.config["V2_SUPABASE"],
            memory=app.config.get("V2_MEMORY"),
        )
        # Allow either ?by=psid or ?by=conversation_id; default to psid.
        by = (request.args.get("by") or "psid").lower()
        if by == "conversation_id":
            payload = api.get_case(
                _build_admin_context(app), conversation_id=identifier,
            )
        else:
            payload = api.get_case(_build_admin_context(app), psid=identifier)
        return jsonify(_strip_raw_psid(payload)), 200

    @app.route("/admin/dashboard/posts", methods=["GET"])
    def dashboard_list_posts():
        denied = _check_admin_token(app)
        if denied:
            return denied
        try:
            limit = int(request.args.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        api = AdminDashboardAPI(
            supabase=app.config["V2_SUPABASE"],
            memory=app.config.get("V2_MEMORY"),
        )
        payload = api.list_recent_posts(_build_admin_context(app), limit=limit)
        return jsonify(_strip_raw_psid(payload)), 200

    @app.route("/admin/dashboard/handoffs", methods=["GET"])
    def dashboard_list_handoffs():
        denied = _check_admin_token(app)
        if denied:
            return denied
        try:
            limit = int(request.args.get("limit") or 20)
        except (TypeError, ValueError):
            limit = 20
        api = AdminDashboardAPI(
            supabase=app.config["V2_SUPABASE"],
            memory=app.config.get("V2_MEMORY"),
        )
        payload = api.list_open_handoffs(_build_admin_context(app), limit=limit)
        return jsonify(_strip_raw_psid(payload)), 200


__all__ = [
    "register_admin_routes",
]
