"""
v2.lib.admin_ops — Admin Handoff + Memory Control foundation (DEV-2026-05-19-004).

Deterministic helpers (no LLM, no live calls, no secrets) used by a future
admin dashboard / LINE admin command handler to:

    1. List the active customer cases the admin should look at.
    2. Build a "case summary" view-model: display name, conversation state,
       latest memory fields, selected tour lock, latest offer snapshot, open
       handoff.
    3. Pause the bot for a specific customer (admin takes over).
    4. Resume the bot for a specific customer.
    5. Inspect / list open handoffs without leaking secrets or wholesale
       partner names.

All functions take the Supabase-like + optional MemoryService as arguments so
unit tests can run with the in-memory fakes from `v2/tests/conftest.py` — no
module-level globals, no env reads, no network.

Hard rules (enforced by `v2/tests/test_admin_ops.py`):

    - Never expose wholesale partner names. Case summaries pass tour names
      through the same wholesale-blacklist regex as `response_writer`; any
      hit is replaced with a redaction token.
    - Never print or log raw access tokens / API keys. PSIDs are masked via
      `redactor.mask_psid` when surfaced in human-readable strings.
    - Never make live OpenAI or paid-provider calls.
    - Never deploy, never touch V1, never reactivate Make.com.
    - Conversation.is_human_paused is kept in sync with the active row in
      `bot_pauses` so the existing orchestrator pause-guard
      (`v2/lib/orchestrator.py` § "Bot pause guard") keeps working unchanged.

This module deliberately does not include a UI. A JSON/view-model layer is
enough — a dashboard or admin LINE command can consume `AdminCaseSummary` and
`list_admin_cases` as-is.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Protocol

from . import redactor
from .response_writer import _WHOLESALE_BLACKLIST  # reuse single source of truth
from .state_machine import State, is_silent_state

logger = logging.getLogger("v2.admin_ops")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class SupabaseLike(Protocol):
    def table(self, name: str): ...  # returns a query-builder object


@dataclass
class SelectedTourBrief:
    """Customer-visible projection of the selected_tours lock (no wholesale)."""
    tour_id: str
    web_code: Optional[str]
    tour_code_real: Optional[str]
    name: Optional[str]
    price: Optional[int]
    selected_at: Optional[str]
    booking_status: Optional[str] = None
    is_fee_acknowledged: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LatestOfferBrief:
    """Trimmed offer snapshot for admin view (top tour only, no wholesale)."""
    id: str
    presented_at: Optional[str]
    tour_count: int
    top_tour_web_code: Optional[str]
    top_tour_name: Optional[str]
    top_tour_price: Optional[int]
    was_selected: bool = False
    selected_rank: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OpenHandoffBrief:
    """Open (unresolved) handoff row, safe for admin display."""
    id: str
    psid_masked: str
    conversation_id: Optional[str]
    triggered_at: Optional[str]
    trigger_type: str
    trigger_detail_summary: Optional[str]  # short string; raw JSON never exposed

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AdminCaseSummary:
    """
    The single record an admin sees per customer/conversation. View-model
    only — no auth, no PII beyond what an admin needs (display name,
    masked PSID, memory snapshot, selected tour, latest offer, open handoff).
    """
    psid: str
    psid_masked: str
    customer_id: Optional[str]
    display_name: Optional[str]

    conversation_id: Optional[str]
    conversation_state: Optional[str]
    last_activity_at: Optional[str]

    is_paused: bool
    paused_until: Optional[str]
    paused_reason: Optional[str]
    paused_by: Optional[str] = None

    is_silent: bool = False  # union of state-silent + active pause

    latest_country: Optional[str] = None
    latest_city: Optional[str] = None
    budget_per_person: Optional[int] = None
    pax_count: Optional[int] = None
    travel_month: Optional[str] = None
    airline_preference: Optional[str] = None

    selected_tour: Optional[SelectedTourBrief] = None
    latest_offer: Optional[LatestOfferBrief] = None
    open_handoff: Optional[OpenHandoffBrief] = None

    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        # nested dataclass already serialised by asdict; ensure no module obj
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Default TTL for an admin-triggered bot pause. Two hours matches typical
# admin chat-coverage windows; resume() can be called any time before then.
DEFAULT_PAUSE_TTL_MINUTES = 120

# Hard upper-bound — refuse to insert a pause longer than 24 h without a
# Codex/Tiw decision (prevents an accidental forever-pause).
MAX_PAUSE_TTL_MINUTES = 24 * 60

_WHOLESALE_REDACTION_TOKEN = "***WHOLESALE-REDACTED***"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _add_minutes_iso(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _is_iso_in_future(iso_str: Optional[str]) -> bool:
    if not iso_str:
        return False
    try:
        # Accept naive ISO too (treat as UTC).
        ts = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts > datetime.now(timezone.utc)
    except Exception:
        return False


def _scrub_wholesale(value: Optional[str]) -> Optional[str]:
    """Redact wholesale brand tokens from a free-text field."""
    if not value:
        return value
    for pat in _WHOLESALE_BLACKLIST:
        if pat.search(value):
            return _WHOLESALE_REDACTION_TOKEN
    return value


def _summarize_trigger_detail(detail: Any) -> Optional[str]:
    """Convert handoff trigger_detail JSON to a short admin-safe string."""
    if not detail:
        return None
    if isinstance(detail, dict):
        # Pick a handful of safe keys; never echo a raw token or psid.
        bits = []
        for k in ("reason", "missing_field", "note"):
            v = detail.get(k)
            if v:
                bits.append(f"{k}={_scrub_wholesale(str(v))}")
        if not bits:
            return None
        out = "; ".join(bits)
        return redactor.redact(out)
    return redactor.redact(_scrub_wholesale(str(detail)) or "")


def _active_conversation(supabase: SupabaseLike, psid: str) -> Optional[dict]:
    """Return the open conversation row for a psid (closed_at IS NULL)."""
    return supabase.table("conversations").select_one(
        {"psid": psid, "closed_at": None}
    )


def _customer_row(supabase: SupabaseLike, psid: str) -> Optional[dict]:
    return supabase.table("customers").select_one({"psid": psid})


def _active_pause_row(supabase: SupabaseLike, psid: str) -> Optional[dict]:
    """Return the active bot_pauses row (resumed_at IS NULL) if any."""
    return supabase.table("bot_pauses").select_one(
        {"psid": psid, "resumed_at": None}
    )


def _open_handoff_row(supabase: SupabaseLike, psid: str) -> Optional[dict]:
    """Return the most recent open handoff (resolution IS NULL)."""
    return supabase.table("handoffs").select_latest(
        {"psid": psid, "resolution": None}, order_by="triggered_at"
    )


def _selected_tour_brief(memory, supabase: SupabaseLike, psid: str) -> Optional[SelectedTourBrief]:
    """Build a SelectedTourBrief from MemoryService + raw row."""
    raw = supabase.table("selected_tours").select_one(
        {"psid": psid, "unlocked_at": None}
    )
    if not raw:
        return None
    tour = supabase.table("tours_canonical").select_one({"id": raw["tour_id"]}) or {}
    name = _scrub_wholesale(tour.get("name"))
    return SelectedTourBrief(
        tour_id=str(raw["tour_id"]),
        web_code=tour.get("web_code"),
        tour_code_real=raw.get("tour_code_real") or tour.get("tour_code_real"),
        name=name,
        price=tour.get("base_price"),
        selected_at=str(raw.get("selected_at") or ""),
        booking_status=raw.get("booking_status"),
        is_fee_acknowledged=bool(raw.get("is_fee_acknowledged", False)),
    )


def _latest_offer_brief(supabase: SupabaseLike, psid: str) -> Optional[LatestOfferBrief]:
    row = supabase.table("offer_snapshots").select_latest(
        {"psid": psid}, order_by="presented_at"
    )
    if not row:
        return None
    tours = row.get("tour_list") or []
    top = tours[0] if tours else {}
    return LatestOfferBrief(
        id=str(row.get("id") or ""),
        presented_at=str(row.get("presented_at") or ""),
        tour_count=len(tours),
        top_tour_web_code=top.get("web_code"),
        top_tour_name=_scrub_wholesale(top.get("name")),
        top_tour_price=top.get("price"),
        was_selected=bool(row.get("was_selected", False)),
        selected_rank=row.get("selected_rank"),
    )


def _open_handoff_brief(supabase: SupabaseLike, psid: str) -> Optional[OpenHandoffBrief]:
    row = _open_handoff_row(supabase, psid)
    if not row:
        return None
    return OpenHandoffBrief(
        id=str(row.get("id") or ""),
        psid_masked=redactor.mask_psid(psid),
        conversation_id=row.get("conversation_id"),
        triggered_at=str(row.get("triggered_at") or ""),
        trigger_type=str(row.get("trigger_type") or "unknown"),
        trigger_detail_summary=_summarize_trigger_detail(row.get("trigger_detail")),
    )


# ---------------------------------------------------------------------------
# Pause / resume
# ---------------------------------------------------------------------------


@dataclass
class PauseResult:
    psid: str
    psid_masked: str
    conversation_id: Optional[str]
    pause_id: str
    pause_until: str
    reason: Optional[str]
    paused_by: str
    handoff_id: Optional[str] = None  # if a handoff row was created
    already_paused: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResumeResult:
    psid: str
    psid_masked: str
    conversation_id: Optional[str]
    pause_id: Optional[str]
    resumed_at: Optional[str]
    resumed_by: str
    new_state: Optional[str]
    handoffs_closed: int = 0
    was_paused: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def pause_bot_for_customer(
    supabase: SupabaseLike,
    *,
    psid: str,
    paused_by: str,
    reason: Optional[str] = None,
    ttl_minutes: int = DEFAULT_PAUSE_TTL_MINUTES,
    record_handoff_if_missing: bool = True,
    handoff_trigger_type: str = "human_request",
) -> PauseResult:
    """
    Pause the bot for `psid` until now()+ttl_minutes.

    Effects (atomic from the caller's POV; idempotent on repeated calls):

        1. Insert a fresh bot_pauses row (resumed_at IS NULL).
        2. Update the active conversation:
              is_human_paused=True, paused_until, paused_reason,
              state='human_paused'.
        3. Optionally insert a handoffs row with trigger_type=`handoff_trigger_type`
           if none is currently open.

    Args:
        psid: Facebook page-scoped ID. Required.
        paused_by: 'admin', 'system', or 'rule'. Required.
        reason: free-text note (admin-facing only). Optional.
        ttl_minutes: pause duration. Defaults 120, hard-capped at 24 h.
        record_handoff_if_missing: if True (default), create a handoff row when
            no open handoff exists. Set False for callers that already created
            their own handoff row upstream.
        handoff_trigger_type: trigger_type to use when creating a handoff row.
            Must satisfy the migration 011 CHECK list. Defaults 'human_request'.

    Raises:
        ValueError: psid is empty, paused_by is not one of the allowed values,
            or ttl_minutes is outside (0, MAX_PAUSE_TTL_MINUTES].
    """
    if not psid:
        raise ValueError("pause_bot_for_customer: psid is required")
    if paused_by not in ("admin", "system", "rule"):
        raise ValueError(
            f"pause_bot_for_customer: paused_by must be one of "
            f"('admin','system','rule'); got {paused_by!r}"
        )
    if ttl_minutes <= 0 or ttl_minutes > MAX_PAUSE_TTL_MINUTES:
        raise ValueError(
            f"pause_bot_for_customer: ttl_minutes must be in (0, {MAX_PAUSE_TTL_MINUTES}]; "
            f"got {ttl_minutes}"
        )

    pause_until_iso = _add_minutes_iso(ttl_minutes)
    paused_at_iso = _now_iso()

    conv = _active_conversation(supabase, psid)
    conv_id = conv["id"] if conv else None
    existing = _active_pause_row(supabase, psid)
    already_paused = existing is not None

    pause_id = str(uuid.uuid4())
    pause_row = {
        "id": pause_id,
        "psid": psid,
        "conversation_id": conv_id,
        "paused_at": paused_at_iso,
        "pause_until": pause_until_iso,
        "paused_by": paused_by,
        "reason": reason,
        "resumed_at": None,
        "resumed_by": None,
    }
    supabase.table("bot_pauses").insert(pause_row)

    # Sync the conversation row so the orchestrator pause-guard short-circuits.
    if conv_id:
        supabase.table("conversations").update(
            {"id": conv_id},
            {
                "is_human_paused": True,
                "paused_until": pause_until_iso,
                "paused_reason": reason,
                "state": State.HUMAN_PAUSED.value,
                "last_activity_at": paused_at_iso,
            },
        )

    handoff_id: Optional[str] = None
    if record_handoff_if_missing:
        open_h = _open_handoff_row(supabase, psid)
        if not open_h and conv_id:
            handoff_id = str(uuid.uuid4())
            supabase.table("handoffs").insert({
                "id": handoff_id,
                "conversation_id": conv_id,
                "psid": psid,
                "triggered_at": paused_at_iso,
                "trigger_type": handoff_trigger_type,
                "trigger_detail": {"reason": reason} if reason else {},
                "bot_paused_until": pause_until_iso,
                "resolution": None,
            })
        elif open_h:
            handoff_id = open_h.get("id")

    logger.info(
        "admin_ops.pause psid=%s by=%s until=%s reason=%s already=%s",
        redactor.mask_psid(psid), paused_by, pause_until_iso,
        redactor.redact(reason or ""), already_paused,
    )

    return PauseResult(
        psid=psid,
        psid_masked=redactor.mask_psid(psid),
        conversation_id=conv_id,
        pause_id=pause_id,
        pause_until=pause_until_iso,
        reason=reason,
        paused_by=paused_by,
        handoff_id=handoff_id,
        already_paused=already_paused,
    )


def resume_bot_for_customer(
    supabase: SupabaseLike,
    *,
    psid: str,
    resumed_by: str,
    reason: Optional[str] = None,
    new_state: str = State.COLLECTING_PREFERENCES.value,
    close_open_handoffs: bool = True,
    handoff_resolution: str = "bot_resumed",
) -> ResumeResult:
    """
    Resume the bot for `psid`. Closes the active pause and updates the
    conversation back to a non-silent state.

    Args:
        psid: required.
        resumed_by: admin identifier (free-text; redacted in logs).
        reason: free-text note. Optional.
        new_state: state to set the conversation to after resume. Defaults
            'collecting_preferences' (matches state_machine._from_human_paused).
            Must not be a silent state.
        close_open_handoffs: if True (default), mark any open handoff for this
            psid as resolved with resolution=`handoff_resolution`.
        handoff_resolution: resolution string to use. Must satisfy migration
            011 CHECK. Defaults 'bot_resumed'.

    Raises:
        ValueError: if psid empty, or new_state is a silent state.
    """
    if not psid:
        raise ValueError("resume_bot_for_customer: psid is required")

    # Refuse to resume INTO a silent state — that would be self-defeating.
    try:
        target_state = State(new_state)
    except ValueError as e:
        raise ValueError(
            f"resume_bot_for_customer: new_state {new_state!r} is not a known State"
        ) from e
    if is_silent_state(target_state):
        raise ValueError(
            f"resume_bot_for_customer: cannot resume INTO silent state {new_state!r}"
        )

    resumed_at = _now_iso()
    pause_row = _active_pause_row(supabase, psid)
    was_paused = pause_row is not None
    pause_id = pause_row["id"] if pause_row else None

    if pause_row:
        patch = {
            "resumed_at": resumed_at,
            "resumed_by": resumed_by,
        }
        if reason and not pause_row.get("reason"):
            patch["reason"] = reason
        supabase.table("bot_pauses").update(
            {"id": pause_row["id"]}, patch,
        )

    conv = _active_conversation(supabase, psid)
    conv_id = conv["id"] if conv else None
    if conv_id:
        supabase.table("conversations").update(
            {"id": conv_id},
            {
                "is_human_paused": False,
                "paused_until": None,
                "paused_reason": None,
                "state": new_state,
                "last_activity_at": resumed_at,
            },
        )

    closed_count = 0
    if close_open_handoffs:
        # The in-memory fake's update(where, patch) updates by equality, so we
        # filter rows ourselves and update each by id.
        opens = supabase.table("handoffs").select_all(
            {"psid": psid, "resolution": None}
        ) if hasattr(supabase.table("handoffs"), "select_all") else []
        for row in opens:
            supabase.table("handoffs").update(
                {"id": row["id"]},
                {
                    "resolution": handoff_resolution,
                    "resolution_at": resumed_at,
                    "admin_responded_at": row.get("admin_responded_at") or resumed_at,
                    "admin_responder": row.get("admin_responder") or resumed_by,
                },
            )
            closed_count += 1

    logger.info(
        "admin_ops.resume psid=%s by=%s reason=%s closed_handoffs=%d was_paused=%s",
        redactor.mask_psid(psid), redactor.redact(resumed_by),
        redactor.redact(reason or ""), closed_count, was_paused,
    )

    return ResumeResult(
        psid=psid,
        psid_masked=redactor.mask_psid(psid),
        conversation_id=conv_id,
        pause_id=pause_id,
        resumed_at=resumed_at if was_paused else None,
        resumed_by=resumed_by,
        new_state=new_state if conv_id else None,
        handoffs_closed=closed_count,
        was_paused=was_paused,
    )


def is_bot_paused_for(supabase: SupabaseLike, psid: str) -> bool:
    """
    Defense-in-depth check used by callers that don't already hold the
    conversation row (e.g. a future admin command bot). Returns True if either:

        - an active bot_pauses row exists (resumed_at IS NULL AND pause_until in future), OR
        - the active conversation has is_human_paused=True.

    The orchestrator's existing short-circuit (`conv.is_human_paused`) already
    covers the inbound webhook path; this function is for callers without that
    object in hand.
    """
    pause = _active_pause_row(supabase, psid)
    if pause and _is_iso_in_future(pause.get("pause_until")):
        return True
    conv = _active_conversation(supabase, psid)
    if conv and conv.get("is_human_paused"):
        return True
    return False


# ---------------------------------------------------------------------------
# Case summary
# ---------------------------------------------------------------------------


def _display_name(memory_view, customer_row: Optional[dict], psid: str) -> Optional[str]:
    """Best-effort admin display name: customer_memory.customer_name → customers.fb_name → masked PSID."""
    if memory_view is not None:
        name = getattr(memory_view, "customer_name", None)
        if name:
            return _scrub_wholesale(name)
    if customer_row:
        fb = customer_row.get("fb_name")
        if fb:
            return _scrub_wholesale(fb)
    return f"Customer {redactor.mask_psid(psid)}"


def get_admin_case(
    supabase: SupabaseLike,
    *,
    psid: Optional[str] = None,
    conversation_id: Optional[str] = None,
    memory=None,
) -> Optional[AdminCaseSummary]:
    """
    Build an AdminCaseSummary for one customer/conversation.

    Pass `psid` or `conversation_id` (psid wins if both given). Returns None
    if no customer exists for the identifier.

    `memory` is an optional MemoryService instance; if provided, its cached
    view is used for the memory snapshot. If None, the function reads
    `customer_memory` directly via `supabase`.
    """
    if not psid and not conversation_id:
        raise ValueError("get_admin_case: pass psid or conversation_id")

    if not psid and conversation_id:
        # Resolve psid via conversations
        conv = supabase.table("conversations").select_one({"id": conversation_id})
        if not conv:
            return None
        psid = conv.get("psid")
        if not psid:
            return None

    customer_row = _customer_row(supabase, psid)
    conv = _active_conversation(supabase, psid)
    pause_row = _active_pause_row(supabase, psid)

    # If nothing about this PSID exists, the admin asked about an unknown
    # customer — return None so the caller can show "not found".
    if not customer_row and not conv and not pause_row:
        return None

    # Memory view (optional dependency)
    memory_view = None
    if memory is not None:
        try:
            memory_view = memory.get_customer_memory(psid)
        except Exception as e:
            logger.warning(
                "admin_ops.get_admin_case: memory.get_customer_memory failed: %s",
                redactor.redact(str(e)),
            )

    cmem_row: dict = {}
    if memory_view is None:
        # Fallback: read row directly (admin path must work even when
        # MemoryService is not wired in by the caller).
        cmem_row = supabase.table("customer_memory").select_one({"psid": psid}) or {}

    def _mem(field_name: str) -> Any:
        if memory_view is not None:
            return getattr(memory_view, field_name, None)
        return cmem_row.get(field_name)

    state_str = conv.get("state") if conv else None
    state_silent = False
    if state_str:
        try:
            state_silent = is_silent_state(State(state_str))
        except ValueError:
            state_silent = False

    selected_tour = _selected_tour_brief(memory, supabase, psid)
    latest_offer = _latest_offer_brief(supabase, psid)
    open_handoff = _open_handoff_brief(supabase, psid)

    is_paused = bool(
        (conv and conv.get("is_human_paused"))
        or (pause_row and _is_iso_in_future(pause_row.get("pause_until")))
    )

    return AdminCaseSummary(
        psid=psid,
        psid_masked=redactor.mask_psid(psid),
        customer_id=customer_row.get("id") if customer_row else None,
        display_name=_display_name(memory_view, customer_row, psid),
        conversation_id=conv.get("id") if conv else None,
        conversation_state=state_str,
        last_activity_at=conv.get("last_activity_at") if conv else None,
        is_paused=is_paused,
        paused_until=(conv or {}).get("paused_until") or (pause_row or {}).get("pause_until"),
        paused_reason=(conv or {}).get("paused_reason") or (pause_row or {}).get("reason"),
        paused_by=(pause_row or {}).get("paused_by"),
        is_silent=is_paused or state_silent,
        latest_country=_mem("latest_country"),
        latest_city=_mem("latest_city"),
        budget_per_person=_mem("budget_per_person"),
        pax_count=_mem("pax_count"),
        travel_month=_mem("travel_month"),
        airline_preference=_mem("airline_preference"),
        selected_tour=selected_tour,
        latest_offer=latest_offer,
        open_handoff=open_handoff,
    )


def list_admin_cases(
    supabase: SupabaseLike,
    *,
    memory=None,
    limit: int = 50,
    only_open: bool = True,
    only_paused: bool = False,
) -> list[AdminCaseSummary]:
    """
    Return up to `limit` admin case summaries, newest first by last_activity_at.

    Args:
        only_open: if True (default), exclude conversations with closed_at set.
        only_paused: if True, only return cases currently paused (is_paused=True).
        limit: hard cap (defaults 50).
    """
    rows = supabase.table("conversations").select_all({}) \
        if hasattr(supabase.table("conversations"), "select_all") else []
    if only_open:
        rows = [r for r in rows if not r.get("closed_at")]

    # Newest first by last_activity_at (string-sortable ISO).
    rows.sort(key=lambda r: r.get("last_activity_at") or r.get("started_at") or "", reverse=True)

    out: list[AdminCaseSummary] = []
    for r in rows:
        if len(out) >= limit:
            break
        psid = r.get("psid")
        if not psid:
            continue
        summary = get_admin_case(supabase, psid=psid, memory=memory)
        if summary is None:
            continue
        if only_paused and not summary.is_paused:
            continue
        out.append(summary)
    return out


def list_open_handoffs(
    supabase: SupabaseLike,
    *,
    limit: int = 50,
) -> list[OpenHandoffBrief]:
    """
    Return open handoffs (resolution IS NULL) newest first, with PSIDs masked
    and trigger_detail summarised to a short safe string. Wholesale tokens are
    redacted from any free-text fields.
    """
    rows = supabase.table("handoffs").select_all({"resolution": None}) \
        if hasattr(supabase.table("handoffs"), "select_all") else []
    rows.sort(key=lambda r: r.get("triggered_at") or "", reverse=True)

    out: list[OpenHandoffBrief] = []
    for r in rows[: max(0, limit)]:
        psid = str(r.get("psid") or "")
        out.append(OpenHandoffBrief(
            id=str(r.get("id") or ""),
            psid_masked=redactor.mask_psid(psid) if psid else "***PSID***",
            conversation_id=r.get("conversation_id"),
            triggered_at=str(r.get("triggered_at") or ""),
            trigger_type=str(r.get("trigger_type") or "unknown"),
            trigger_detail_summary=_summarize_trigger_detail(r.get("trigger_detail")),
        ))
    return out


def record_handoff(
    supabase: SupabaseLike,
    *,
    psid: str,
    trigger_type: str,
    trigger_detail: Optional[dict] = None,
    conversation_id: Optional[str] = None,
) -> str:
    """
    Insert a handoffs row. Returns the new id. Intended for callers that need
    to register a handoff WITHOUT pausing (rare; usually pause_bot_for_customer
    handles this).
    """
    if not psid:
        raise ValueError("record_handoff: psid is required")
    valid_types = {
        "attachment", "fee_missing", "human_request", "payment",
        "booking_confirm", "low_confidence", "error", "sla_breach",
    }
    if trigger_type not in valid_types:
        raise ValueError(
            f"record_handoff: trigger_type must be one of {sorted(valid_types)}; "
            f"got {trigger_type!r}"
        )
    if conversation_id is None:
        conv = _active_conversation(supabase, psid)
        conversation_id = conv["id"] if conv else None
    if not conversation_id:
        raise ValueError("record_handoff: no active conversation for psid")

    hid = str(uuid.uuid4())
    supabase.table("handoffs").insert({
        "id": hid,
        "conversation_id": conversation_id,
        "psid": psid,
        "triggered_at": _now_iso(),
        "trigger_type": trigger_type,
        "trigger_detail": trigger_detail or {},
        "resolution": None,
    })
    return hid


__all__ = [
    "AdminCaseSummary",
    "SelectedTourBrief",
    "LatestOfferBrief",
    "OpenHandoffBrief",
    "PauseResult",
    "ResumeResult",
    "DEFAULT_PAUSE_TTL_MINUTES",
    "MAX_PAUSE_TTL_MINUTES",
    "pause_bot_for_customer",
    "resume_bot_for_customer",
    "is_bot_paused_for",
    "get_admin_case",
    "list_admin_cases",
    "list_open_handoffs",
    "record_handoff",
]
