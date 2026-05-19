"""
v2.webhook.test_mode_gate - admin-only real chat readiness helpers.

This module is intentionally deterministic and side-effect free. It never
calls Meta, LINE, OpenAI, OCR providers, Supabase, or Redis. The webhook uses
it as a fail-closed guard before scheduling background processing when
`V2_ADMIN_ONLY_TEST_MODE=true`.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Iterable, Mapping, Optional

from ..lib import redactor

_ENV_ADMIN_ONLY = "V2_ADMIN_ONLY_TEST_MODE"
_ENV_ADMIN_TEST_PSIDS = "V2_STAGING_ADMIN_TEST_PSID_ALLOW_LIST"
_ENV_ADMIN_TEST_PSIDS_ALT = "V2_STAGING_ADMIN_TEST_PSIDS"
_ENV_DASHBOARD_TOKEN = "V2_STAGING_DASHBOARD_TOKEN"
_ENV_LINE_ALLOW_LIST = "V2_STAGING_LINE_ADMIN_ALLOW_LIST"
_ENV_LINE_ALLOW_LIST_ALT = "V2_STAGING_LINE_ADMIN_USER_OR_GROUP_ID"


@dataclass(frozen=True)
class AdminOnlyGateDecision:
    allowed: bool
    reason: str
    admin_only_mode: bool
    psid_masked: Optional[str] = None
    allow_list_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def parse_id_list(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set, frozenset)):
        parts = []
        for item in value:
            parts.extend(parse_id_list(item))
        return tuple(dict.fromkeys(parts))
    text = str(value)
    for sep in (";", "\n", "\t", " "):
        text = text.replace(sep, ",")
    out = []
    for part in text.split(","):
        s = part.strip()
        if not s:
            continue
        # PSIDs are opaque; reject control chars and absurdly long entries.
        if any(c in s for c in ("\r", "\n", "\t")) or len(s) > 128:
            continue
        out.append(s)
    return tuple(dict.fromkeys(out))


def _config_get(config: Optional[Mapping], key: str, default=None):
    if not config:
        return default
    try:
        return config.get(key, default)
    except AttributeError:
        return getattr(config, key, default)


def admin_only_enabled(config: Optional[Mapping] = None, env: Optional[Mapping] = None) -> bool:
    env = env or os.environ
    injected = _config_get(config, "V2_ADMIN_ONLY_TEST_MODE", None)
    if injected is not None:
        return parse_bool(injected)
    return parse_bool(env.get(_ENV_ADMIN_ONLY))


def admin_test_psids(config: Optional[Mapping] = None, env: Optional[Mapping] = None) -> tuple[str, ...]:
    env = env or os.environ
    injected = _config_get(config, "V2_ADMIN_TEST_PSID_ALLOW_LIST", None)
    if injected is not None:
        return parse_id_list(injected)
    primary = env.get(_ENV_ADMIN_TEST_PSIDS)
    if primary:
        return parse_id_list(primary)
    return parse_id_list(env.get(_ENV_ADMIN_TEST_PSIDS_ALT))


def should_process_inbound(psid: Optional[str], *, config: Optional[Mapping] = None,
                           env: Optional[Mapping] = None) -> AdminOnlyGateDecision:
    enabled = admin_only_enabled(config=config, env=env)
    masked = redactor.mask_psid(str(psid)) if psid else None
    if not enabled:
        return AdminOnlyGateDecision(
            allowed=True,
            reason="admin_only_disabled",
            admin_only_mode=False,
            psid_masked=masked,
            allow_list_count=0,
        )

    allowed_ids = admin_test_psids(config=config, env=env)
    if not allowed_ids:
        return AdminOnlyGateDecision(
            allowed=False,
            reason="missing_admin_test_psid_allow_list",
            admin_only_mode=True,
            psid_masked=masked,
            allow_list_count=0,
        )
    if psid and str(psid) in set(allowed_ids):
        return AdminOnlyGateDecision(
            allowed=True,
            reason="admin_test_psid_allowed",
            admin_only_mode=True,
            psid_masked=masked,
            allow_list_count=len(allowed_ids),
        )
    return AdminOnlyGateDecision(
        allowed=False,
        reason="psid_not_allowlisted_for_admin_only_test",
        admin_only_mode=True,
        psid_masked=masked,
        allow_list_count=len(allowed_ids),
    )


def _presence_status(value) -> str:
    ids = getattr(value, "ids", None)
    if ids is not None:
        try:
            return "configured" if len(ids) > 0 else "missing"
        except TypeError:
            return "configured" if bool(ids) else "missing"
    if isinstance(value, (list, tuple, set, frozenset)):
        return "configured" if len(value) > 0 else "missing"
    return "configured" if bool(value) else "missing"


def runtime_config_status(*, app_config: Optional[Mapping] = None,
                          runtime_config=None,
                          env: Optional[Mapping] = None) -> dict:
    """
    Safe readiness view for admin-only testing.

    Values are status strings/counts only. Tokens, keys, and raw PSIDs are
    never echoed.
    """
    env = env or os.environ
    mode = admin_only_enabled(config=app_config, env=env)
    psids = admin_test_psids(config=app_config, env=env)

    dashboard_token = _config_get(app_config, "V2_ADMIN_TOKEN", None) or env.get(_ENV_DASHBOARD_TOKEN)
    line_allow = (
        _config_get(app_config, "V2_ADMIN_ALLOW_LIST", None)
        or env.get(_ENV_LINE_ALLOW_LIST)
        or env.get(_ENV_LINE_ALLOW_LIST_ALT)
    )

    supabase_url = (
        getattr(runtime_config, "supabase_url", None)
        or env.get("V2_STAGING_SUPABASE_URL")
    )
    fb_secret = (
        getattr(runtime_config, "fb_app_secret", None)
        or env.get("V2_STAGING_FB_APP_SECRET")
    )
    fb_verify = (
        getattr(runtime_config, "fb_verify_token", None)
        or env.get("V2_STAGING_FB_VERIFY_TOKEN")
    )

    admin_psid_status = "configured" if psids else ("missing" if mode else "disabled")
    checks = {
        "admin_only_test_mode": "enabled" if mode else "disabled",
        "admin_test_psid_allow_list": admin_psid_status,
        "admin_test_psid_allow_list_count": len(psids),
        "dashboard_admin_token": _presence_status(dashboard_token),
        "line_admin_allow_list": _presence_status(line_allow),
        "supabase_staging_url": _presence_status(supabase_url),
        "fb_app_secret": _presence_status(fb_secret),
        "fb_verify_token": _presence_status(fb_verify),
    }
    ready = (
        checks["dashboard_admin_token"] == "configured"
        and checks["line_admin_allow_list"] == "configured"
        and checks["supabase_staging_url"] == "configured"
        and checks["fb_app_secret"] == "configured"
        and checks["fb_verify_token"] == "configured"
        and (not mode or bool(psids))
    )
    return {
        "ok": bool(ready),
        "status": "ok" if ready else "missing",
        "checks": checks,
    }


__all__ = [
    "AdminOnlyGateDecision",
    "admin_only_enabled",
    "admin_test_psids",
    "parse_bool",
    "parse_id_list",
    "runtime_config_status",
    "should_process_inbound",
]
