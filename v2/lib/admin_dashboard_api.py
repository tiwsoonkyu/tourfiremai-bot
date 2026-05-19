"""
v2.lib.admin_dashboard_api — DEV-2026-05-19-008.

Minimal, safe read surface for the future V2 admin dashboard. This module
is a *service layer*, not a web framework, so it can be exercised by tests
without spinning up Flask and so a later HTTP shim can plug a chosen
framework on top without losing the safety contract here.

Surface:

    AdminContext(admin_user_id, allowed)  — auth/admin context
    AdminDashboardAPI(supabase, memory=None)
        .list_cases(context, *, limit=20, only_paused=False)   → dict
        .get_case(context, *, psid=None, conversation_id=None) → dict
        .list_recent_posts(context, *, limit=10)               → dict
        .list_open_handoffs(context, *, limit=20)              → dict

Hard rules (enforced by `v2/tests/test_admin_dashboard_api.py`):

    - Every method REQUIRES an `AdminContext` whose `.allowed` is True.
      A missing or non-admin context is denied with action='denied'.
    - Returned payloads must not include:
        * raw PSIDs (always masked via `redactor.mask_psid`)
        * raw conversation history / message text
        * raw page-post captions (titles only, capped by
          `page_post_context.CONTEXT_TITLE_MAX_CHARS`)
        * wholesale partner brand names (scrubbed)
        * secrets, tokens, or env values
    - The customer display name preferred over the masked PSID; PSIDs only
      surface in `psid_masked`.
    - Payloads are compact: lists are capped, and per-row payloads use the
      already-safe view-models from `admin_ops` / `page_post_context`.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from . import redactor
from .admin_ops import (
    AdminCaseSummary,
    get_admin_case,
    list_admin_cases,
    list_open_handoffs,
)
from .page_post_context import (
    CONTEXT_TITLE_MAX_CHARS,
    PagePostSummary,
    list_recent_page_posts,
)
from .response_writer import _WHOLESALE_BLACKLIST

logger = logging.getLogger("v2.admin_dashboard_api")

_WHOLESALE_REDACTION_TOKEN = "***WHOLESALE-REDACTED***"

# Defensive caps so a misuse of `limit` cannot produce a giant payload.
_HARD_LIST_LIMIT = 100
_DEFAULT_CASES_LIMIT = 20
_DEFAULT_POSTS_LIMIT = 10
_DEFAULT_HANDOFFS_LIMIT = 20


@dataclass(frozen=True)
class AdminContext:
    """
    Auth/admin context. The dashboard layer above this module must build
    one and pass it to every API call. Tests can build one directly.

    `allowed=True` is what gates every read. `admin_user_id` is recorded
    in audit logs but never returned in payloads.
    """
    admin_user_id: Optional[str]
    allowed: bool = False
    source: str = "test"  # 'line' / 'web' / 'test' — for audit only

    def to_dict(self) -> dict:
        # NEVER include the user id; only its truthiness.
        return {"allowed": bool(self.allowed), "source": self.source,
                "has_user_id": bool(self.admin_user_id)}


def _denied(reason: str) -> dict:
    return {
        "ok": False,
        "action": "denied",
        "error": reason,
        "data": None,
    }


def _scrub_wholesale(value):
    if not value:
        return value
    out = str(value)
    for pat in _WHOLESALE_BLACKLIST:
        out = pat.sub(_WHOLESALE_REDACTION_TOKEN, out)
    return out


def _safe_text(value):
    return redactor.redact(_scrub_wholesale(value) or "") if value else value


def _serialise_case(case: AdminCaseSummary) -> dict:
    """
    Project an AdminCaseSummary into a compact dashboard row.

    NEVER includes raw PSID — the dashboard reads `psid_masked` and uses
    `customer_id` / `conversation_id` for cross-references.
    """
    selected = case.selected_tour.to_dict() if case.selected_tour else None
    offer = case.latest_offer.to_dict() if case.latest_offer else None
    handoff = case.open_handoff.to_dict() if case.open_handoff else None

    # Re-scrub the name fields defensively — admin_ops already does this,
    # but this layer is the public boundary so we do not trust upstream.
    if selected and selected.get("name"):
        selected["name"] = _scrub_wholesale(selected["name"])
    if offer and offer.get("top_tour_name"):
        offer["top_tour_name"] = _scrub_wholesale(offer["top_tour_name"])

    display_name = _safe_text(case.display_name) or case.psid_masked

    return {
        "psid_masked": case.psid_masked,
        "customer_id": case.customer_id,
        "display_name": display_name,
        "conversation_id": case.conversation_id,
        "conversation_state": case.conversation_state,
        "last_activity_at": case.last_activity_at,
        "is_paused": bool(case.is_paused),
        "is_silent": bool(case.is_silent),
        "paused_until": case.paused_until,
        "paused_reason": _safe_text(case.paused_reason),
        "paused_by": case.paused_by,
        "latest_country": _safe_text(case.latest_country),
        "latest_city": _safe_text(case.latest_city),
        "budget_per_person": case.budget_per_person,
        "pax_count": case.pax_count,
        "travel_month": _safe_text(case.travel_month),
        "airline_preference": _safe_text(case.airline_preference),
        "selected_tour": selected,
        "latest_offer": offer,
        "open_handoff": handoff,
        "notes": [_safe_text(n) for n in case.notes],
    }


def _serialise_post(summary: PagePostSummary) -> dict:
    """
    Compact, caption-free projection of a page post for the dashboard.

    Title is already capped to `CONTEXT_TITLE_MAX_CHARS` by
    `page_post_context.list_recent_page_posts`, but we re-cap defensively
    and never include `caption_text`.
    """
    title = _safe_text(summary.title or "") or ""
    if len(title) > CONTEXT_TITLE_MAX_CHARS:
        title = title[: CONTEXT_TITLE_MAX_CHARS - 1].rstrip() + "…"
    return {
        "id": summary.id,
        "platform": summary.platform,
        "post_id": summary.post_id,
        "title": title,
        "posted_at": summary.posted_at,
        "permalink_url": _safe_text(summary.permalink_url),
        "source_type": summary.source_type,
        "linked_web_codes": list(summary.linked_web_codes)[:10],
        "linked_tour_codes_real": list(summary.linked_tour_codes_real)[:10],
        "is_post_blocked": bool(summary.is_post_blocked),
        "block_status": summary.block_status,
    }


def _serialise_handoff(handoff) -> dict:
    return {
        "id": handoff.id,
        "psid_masked": handoff.psid_masked,
        "conversation_id": handoff.conversation_id,
        "triggered_at": handoff.triggered_at,
        "trigger_type": handoff.trigger_type,
        "trigger_detail_summary": _safe_text(handoff.trigger_detail_summary),
    }


class AdminDashboardAPI:
    """
    Service layer the dashboard / HTTP shim calls. Stateless — instances
    are cheap to create per request.
    """

    def __init__(self, *, supabase, memory=None) -> None:
        if supabase is None:
            raise ValueError("AdminDashboardAPI: supabase is required")
        self._supabase = supabase
        self._memory = memory

    # --- helpers -------------------------------------------------------

    def _gate(self, context: Optional[AdminContext]) -> Optional[dict]:
        if context is None or not isinstance(context, AdminContext):
            return _denied("missing_admin_context")
        if not context.allowed:
            return _denied("not_allowed")
        return None

    @staticmethod
    def _cap_limit(limit: int, default: int) -> int:
        try:
            n = int(limit)
        except (TypeError, ValueError):
            return default
        if n <= 0:
            return default
        return min(n, _HARD_LIST_LIMIT)

    # --- read endpoints ------------------------------------------------

    def list_cases(self, context: Optional[AdminContext], *,
                   limit: int = _DEFAULT_CASES_LIMIT,
                   only_paused: bool = False) -> dict:
        """Return up to `limit` current case summaries."""
        denied = self._gate(context)
        if denied:
            return denied
        capped = self._cap_limit(limit, _DEFAULT_CASES_LIMIT)
        cases = list_admin_cases(
            self._supabase, memory=self._memory,
            limit=capped, only_open=True, only_paused=only_paused,
        )
        return {
            "ok": True,
            "action": "list_cases",
            "data": {
                "count": len(cases),
                "limit": capped,
                "only_paused": bool(only_paused),
                "cases": [_serialise_case(c) for c in cases],
            },
        }

    def get_case(self, context: Optional[AdminContext], *,
                 psid: Optional[str] = None,
                 conversation_id: Optional[str] = None) -> dict:
        """Return a single case detail. Either psid or conversation_id required."""
        denied = self._gate(context)
        if denied:
            return denied
        if not psid and not conversation_id:
            return {
                "ok": False, "action": "get_case",
                "error": "missing_identifier", "data": None,
            }
        try:
            case = get_admin_case(
                self._supabase,
                psid=psid, conversation_id=conversation_id,
                memory=self._memory,
            )
        except ValueError as e:
            return {
                "ok": False, "action": "get_case",
                "error": "invalid_identifier", "data": None,
            }
        if case is None:
            return {
                "ok": True, "action": "get_case",
                "data": {"case": None}, "error": "case_not_found",
            }
        return {
            "ok": True, "action": "get_case",
            "data": {"case": _serialise_case(case)},
        }

    def list_recent_posts(self, context: Optional[AdminContext], *,
                          limit: int = _DEFAULT_POSTS_LIMIT) -> dict:
        """Return compact recent-page-post summaries (title-only, no captions)."""
        denied = self._gate(context)
        if denied:
            return denied
        capped = self._cap_limit(limit, _DEFAULT_POSTS_LIMIT)
        summaries = list_recent_page_posts(self._supabase, limit=capped)
        return {
            "ok": True,
            "action": "list_recent_posts",
            "data": {
                "count": len(summaries),
                "limit": capped,
                "posts": [_serialise_post(s) for s in summaries],
            },
        }

    def list_open_handoffs(self, context: Optional[AdminContext], *,
                           limit: int = _DEFAULT_HANDOFFS_LIMIT) -> dict:
        """Return compact open-handoff briefs."""
        denied = self._gate(context)
        if denied:
            return denied
        capped = self._cap_limit(limit, _DEFAULT_HANDOFFS_LIMIT)
        handoffs = list_open_handoffs(self._supabase, limit=capped)
        return {
            "ok": True,
            "action": "list_open_handoffs",
            "data": {
                "count": len(handoffs),
                "limit": capped,
                "handoffs": [_serialise_handoff(h) for h in handoffs],
            },
        }


__all__ = [
    "AdminContext",
    "AdminDashboardAPI",
]
