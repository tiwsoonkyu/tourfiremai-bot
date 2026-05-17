"""
v2.lib.state_machine — Deterministic state machine for V2 orchestration.

Spec: docs/V2_STATE_MACHINE.md. 10 states + universal triggers + tool whitelist.

LLM does NOT decide transitions — this code does. LLM is read-only on data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class State(str, Enum):
    NEW_LEAD = "new_lead"
    COLLECTING_PREFERENCES = "collecting_preferences"
    OPTIONS_PRESENTED = "options_presented"
    TOUR_SELECTED = "tour_selected"
    DEPARTURE_SELECTED = "departure_selected"
    FEE_CHECK_REQUIRED = "fee_check_required"
    BOOKING_READY_FOR_HANDOFF = "booking_ready_for_handoff"
    WAITING_TEAM = "waiting_team"
    HUMAN_PAUSED = "human_paused"
    CLOSED = "closed"


# Tools each state may invoke. Orchestrator filters before passing to LLM.
ALLOWED_TOOLS: dict[State, frozenset[str]] = {
    State.NEW_LEAD: frozenset({
        "search_tours", "update_customer_memory", "append_conversation_event",
        "notify_team_line", "mark_waiting_team",
    }),
    State.COLLECTING_PREFERENCES: frozenset({
        "search_tours", "update_customer_memory", "get_tour_detail",
        "append_conversation_event", "notify_team_line", "mark_waiting_team",
    }),
    State.OPTIONS_PRESENTED: frozenset({
        "get_latest_offer_snapshot", "lock_selected_tour",
        "search_tours", "get_tour_detail",
        "append_conversation_event", "notify_team_line", "mark_waiting_team",
    }),
    State.TOUR_SELECTED: frozenset({
        "get_tour_detail", "get_tour_fees", "clear_selected_tour",
        "update_customer_memory", "append_conversation_event",
        "notify_team_line", "mark_waiting_team",
    }),
    State.DEPARTURE_SELECTED: frozenset({
        "get_tour_detail", "get_tour_fees", "append_conversation_event",
        "notify_team_line", "mark_waiting_team",
    }),
    State.FEE_CHECK_REQUIRED: frozenset({
        "get_tour_fees", "append_conversation_event",
        "notify_team_line", "mark_waiting_team",
    }),
    State.BOOKING_READY_FOR_HANDOFF: frozenset({
        "notify_team_line", "mark_waiting_team", "append_conversation_event",
    }),
    State.WAITING_TEAM: frozenset({"check_bot_pause", "append_conversation_event"}),
    State.HUMAN_PAUSED: frozenset({"check_bot_pause"}),
    State.CLOSED: frozenset(),
}


@dataclass(frozen=True)
class Intent:
    """Lightweight subset of v2.lib.intent.Intent for type contract."""
    type: str
    raw_text: str = ""
    country: Optional[str] = None
    budget: Optional[int] = None
    selected_index: Optional[int] = None
    selected_code: Optional[str] = None
    has_attachment: bool = False


@dataclass
class Transition:
    next_state: State
    reason: str
    tool_hints: list[str] = field(default_factory=list)
    needs_unlock_first: bool = False


# --- Universal triggers --------------------------------------------------------

# These intents override state-specific routing and send to waiting_team
_UNIVERSAL_TO_WAITING = {
    "send_attachment": "attachment",
    "ask_human": "human_request",
    "payment_keyword": "payment_keyword",
}

# Closing trigger: explicit "no thanks" → closed
_INTENT_CLOSE = {"decline_final", "off_topic_strong"}


# --- Per-state transition logic -----------------------------------------------

def _from_new_lead(intent: Intent) -> Transition:
    if intent.type in ("greeting",):
        return Transition(State.NEW_LEAD, "ack_greeting", ["update_customer_memory"])
    if intent.type in ("ask_country", "ask_tour_detail") and intent.country:
        return Transition(
            State.COLLECTING_PREFERENCES,
            "got_country",
            ["search_tours", "update_customer_memory"],
        )
    return Transition(State.NEW_LEAD, "still_unknown", [])


def _from_collecting(intent: Intent) -> Transition:
    if intent.type in ("ask_tour_detail",) and intent.country:
        return Transition(
            State.OPTIONS_PRESENTED,
            "present_top_n",
            ["search_tours", "save_offer_snapshot"],
        )
    if intent.type in ("ask_country", "ask_budget", "ask_pax", "ask_period"):
        return Transition(
            State.COLLECTING_PREFERENCES,
            "accumulate_criteria",
            ["update_customer_memory"],
        )
    if intent.type == "select_tour":
        # Block — must go through options_presented first
        return Transition(
            State.COLLECTING_PREFERENCES,
            "blocked_select_without_options",
            [],
        )
    return Transition(State.COLLECTING_PREFERENCES, "noop", [])


def _from_options_presented(intent: Intent) -> Transition:
    if intent.type == "select_tour":
        return Transition(
            State.TOUR_SELECTED,
            "lock_selection",
            ["get_latest_offer_snapshot", "lock_selected_tour"],
        )
    if intent.type in ("ask_country", "ask_budget"):
        return Transition(
            State.COLLECTING_PREFERENCES,
            "change_criteria",
            ["update_customer_memory", "search_tours"],
        )
    if intent.type == "ask_tour_detail":
        return Transition(State.OPTIONS_PRESENTED, "refresh", ["search_tours"])
    return Transition(State.OPTIONS_PRESENTED, "noop", [])


def _from_tour_selected(intent: Intent) -> Transition:
    if intent.type == "select_departure":
        return Transition(
            State.DEPARTURE_SELECTED, "got_departure", ["update_customer_memory"]
        )
    if intent.type == "ask_fee":
        return Transition(
            State.FEE_CHECK_REQUIRED, "ask_fee_triggered", ["get_tour_fees"]
        )
    if intent.type == "select_tour":
        # Different tour → unlock first
        return Transition(
            State.TOUR_SELECTED,
            "swap_tour",
            ["clear_selected_tour", "lock_selected_tour"],
            needs_unlock_first=True,
        )
    if intent.type == "ask_country":
        return Transition(
            State.COLLECTING_PREFERENCES,
            "abandon_selection",
            ["clear_selected_tour"],
            needs_unlock_first=True,
        )
    if intent.type == "ask_tour_detail":
        return Transition(State.TOUR_SELECTED, "show_current", ["get_tour_detail"])
    return Transition(State.TOUR_SELECTED, "noop", [])


def _from_departure_selected(intent: Intent) -> Transition:
    if intent.type == "confirm_booking":
        return Transition(
            State.FEE_CHECK_REQUIRED, "confirm_triggers_fee_check", ["get_tour_fees"]
        )
    if intent.type == "ask_fee":
        return Transition(
            State.FEE_CHECK_REQUIRED, "ask_fee_triggered", ["get_tour_fees"]
        )
    return Transition(State.DEPARTURE_SELECTED, "noop", [])


def _from_fee_check_required(intent: Intent, fee_complete: Optional[bool]) -> Transition:
    if fee_complete is True:
        return Transition(
            State.BOOKING_READY_FOR_HANDOFF, "fee_ok", ["notify_team_line"]
        )
    if fee_complete is False:
        return Transition(
            State.WAITING_TEAM,
            "fee_missing_mandatory_handoff",
            ["notify_team_line", "mark_waiting_team"],
        )
    return Transition(State.FEE_CHECK_REQUIRED, "noop", ["get_tour_fees"])


def _from_booking_ready(intent: Intent) -> Transition:
    return Transition(
        State.WAITING_TEAM,
        "team_notified",
        ["notify_team_line", "mark_waiting_team"],
    )


def _from_waiting_team(intent: Intent, admin_takeover: bool, timeout_reached: bool) -> Transition:
    if admin_takeover:
        return Transition(State.HUMAN_PAUSED, "admin_took_over", [])
    if timeout_reached:
        return Transition(State.CLOSED, "sla_timeout", [])
    return Transition(State.WAITING_TEAM, "still_waiting", [])


def _from_human_paused(intent: Intent, admin_resumed: bool) -> Transition:
    if admin_resumed:
        # Resume to last meaningful state — orchestrator must look up snapshot
        return Transition(State.COLLECTING_PREFERENCES, "admin_resumed_default", [])
    return Transition(State.HUMAN_PAUSED, "still_paused", [])


def _from_closed(intent: Intent) -> Transition:
    # Any new message after closed → start a new conversation as new_lead
    return Transition(State.NEW_LEAD, "reopen_as_new_lead", [])


# --- Public entrypoints --------------------------------------------------------

@dataclass
class StateContext:
    fee_complete: Optional[bool] = None       # True/False/None (unknown)
    admin_takeover: bool = False
    admin_resumed: bool = False
    timeout_reached: bool = False


def transition(current: State, intent: Intent, ctx: Optional[StateContext] = None) -> Transition:
    """
    Pure deterministic transition function.
    Returns (next_state, reason, tool_hints, needs_unlock_first).
    """
    ctx = ctx or StateContext()

    # Universal triggers (highest priority)
    if intent.has_attachment or intent.type == "send_attachment":
        return Transition(
            State.WAITING_TEAM,
            "universal_attachment",
            ["notify_team_line", "mark_waiting_team"],
        )
    if intent.type in _UNIVERSAL_TO_WAITING:
        return Transition(
            State.WAITING_TEAM,
            f"universal_{_UNIVERSAL_TO_WAITING[intent.type]}",
            ["notify_team_line", "mark_waiting_team"],
        )
    if intent.type in _INTENT_CLOSE:
        return Transition(State.CLOSED, "customer_declined", [])

    # State-specific routing
    handler = {
        State.NEW_LEAD: lambda: _from_new_lead(intent),
        State.COLLECTING_PREFERENCES: lambda: _from_collecting(intent),
        State.OPTIONS_PRESENTED: lambda: _from_options_presented(intent),
        State.TOUR_SELECTED: lambda: _from_tour_selected(intent),
        State.DEPARTURE_SELECTED: lambda: _from_departure_selected(intent),
        State.FEE_CHECK_REQUIRED: lambda: _from_fee_check_required(intent, ctx.fee_complete),
        State.BOOKING_READY_FOR_HANDOFF: lambda: _from_booking_ready(intent),
        State.WAITING_TEAM: lambda: _from_waiting_team(intent, ctx.admin_takeover, ctx.timeout_reached),
        State.HUMAN_PAUSED: lambda: _from_human_paused(intent, ctx.admin_resumed),
        State.CLOSED: lambda: _from_closed(intent),
    }
    return handler[current]()


def allowed_tools(state: State) -> frozenset[str]:
    """Return the set of tools the LLM may call when in `state`."""
    return ALLOWED_TOOLS.get(state, frozenset())


def is_silent_state(state: State) -> bool:
    """States where bot must NOT respond to customer."""
    return state in (State.HUMAN_PAUSED, State.CLOSED)
