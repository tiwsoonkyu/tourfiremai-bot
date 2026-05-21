"""Sprint 2 test: state machine transitions."""

import pytest
from v2.lib.state_machine import (
    State, Intent, StateContext, transition, allowed_tools, is_silent_state,
)


def _i(t, **kw):
    return Intent(type=t, raw_text="", **kw)


class TestUniversalTriggers:
    @pytest.mark.parametrize("start", list(State))
    def test_attachment_goes_to_waiting_team(self, start):
        if start in (State.WAITING_TEAM, State.HUMAN_PAUSED, State.CLOSED):
            return  # universal triggers won't move from silent states meaningfully
        result = transition(start, _i("send_attachment", has_attachment=True))
        assert result.next_state == State.WAITING_TEAM

    @pytest.mark.parametrize("start", [State.NEW_LEAD, State.COLLECTING_PREFERENCES,
                                         State.OPTIONS_PRESENTED, State.TOUR_SELECTED])
    def test_ask_human_goes_to_waiting_team(self, start):
        result = transition(start, _i("ask_human"))
        assert result.next_state == State.WAITING_TEAM

    def test_payment_keyword(self):
        result = transition(State.TOUR_SELECTED, _i("payment_keyword"))
        assert result.next_state == State.WAITING_TEAM

    def test_decline_final_closes(self):
        result = transition(State.OPTIONS_PRESENTED, _i("decline_final"))
        assert result.next_state == State.CLOSED


class TestNewLead:
    def test_greeting_stays(self):
        result = transition(State.NEW_LEAD, _i("greeting"))
        assert result.next_state == State.NEW_LEAD

    def test_ask_country_with_country_moves(self):
        result = transition(State.NEW_LEAD, _i("ask_country", country="ญี่ปุ่น"))
        assert result.next_state == State.OPTIONS_PRESENTED
        assert "search_tours" in result.tool_hints

    def test_ask_country_without_country_stays(self):
        result = transition(State.NEW_LEAD, _i("ask_country"))
        assert result.next_state == State.NEW_LEAD


class TestCollectingPreferences:
    def test_ask_country_with_country_presents(self):
        result = transition(State.COLLECTING_PREFERENCES, _i("ask_country", country="ญี่ปุ่น"))
        assert result.next_state == State.OPTIONS_PRESENTED
        assert "search_tours" in result.tool_hints

    def test_ask_tour_detail_with_country_presents(self):
        result = transition(State.COLLECTING_PREFERENCES, _i("ask_tour_detail", country="ญี่ปุ่น"))
        assert result.next_state == State.OPTIONS_PRESENTED
        assert "search_tours" in result.tool_hints

    def test_select_tour_blocked(self):
        result = transition(State.COLLECTING_PREFERENCES, _i("select_tour", selected_index=2))
        assert result.next_state == State.COLLECTING_PREFERENCES
        assert "blocked" in result.reason


class TestOptionsPresented:
    def test_select_locks(self):
        result = transition(State.OPTIONS_PRESENTED, _i("select_tour", selected_index=1))
        assert result.next_state == State.TOUR_SELECTED

    def test_change_criteria_returns_to_collecting(self):
        result = transition(State.OPTIONS_PRESENTED, _i("ask_country", country="เกาหลี"))
        assert result.next_state == State.COLLECTING_PREFERENCES


class TestTourSelected:
    def test_swap_tour_requires_unlock(self):
        result = transition(State.TOUR_SELECTED, _i("select_tour", selected_index=3))
        assert result.next_state == State.TOUR_SELECTED
        assert result.needs_unlock_first is True

    def test_ask_fee_moves_to_fee_check(self):
        result = transition(State.TOUR_SELECTED, _i("ask_fee"))
        assert result.next_state == State.FEE_CHECK_REQUIRED

    def test_select_departure(self):
        result = transition(State.TOUR_SELECTED, _i("select_departure"))
        assert result.next_state == State.DEPARTURE_SELECTED


class TestFeeCheck:
    def test_fee_complete_proceeds(self):
        result = transition(
            State.FEE_CHECK_REQUIRED, _i("confirm_booking"),
            StateContext(fee_complete=True),
        )
        assert result.next_state == State.BOOKING_READY_FOR_HANDOFF

    def test_fee_missing_handoff(self):
        result = transition(
            State.FEE_CHECK_REQUIRED, _i("ask_fee"),
            StateContext(fee_complete=False),
        )
        assert result.next_state == State.WAITING_TEAM


class TestWaitingTeam:
    def test_admin_takeover_pauses(self):
        result = transition(
            State.WAITING_TEAM, _i("greeting"),
            StateContext(admin_takeover=True),
        )
        assert result.next_state == State.HUMAN_PAUSED

    def test_timeout_closes(self):
        result = transition(
            State.WAITING_TEAM, _i("greeting"),
            StateContext(timeout_reached=True),
        )
        assert result.next_state == State.CLOSED


class TestAllowedTools:
    def test_new_lead_no_lock(self):
        tools = allowed_tools(State.NEW_LEAD)
        assert "lock_selected_tour" not in tools
        assert "search_tours" in tools

    def test_options_presented_can_lock(self):
        tools = allowed_tools(State.OPTIONS_PRESENTED)
        assert "lock_selected_tour" in tools
        assert "update_customer_memory" in tools

    def test_present_top_n_hints_are_allowed_after_transition(self):
        result = transition(State.NEW_LEAD, _i("ask_country", country="JP"))
        assert result.next_state == State.OPTIONS_PRESENTED
        assert set(result.tool_hints).issubset(allowed_tools(result.next_state))

    def test_closed_has_no_tools(self):
        assert allowed_tools(State.CLOSED) == frozenset()


class TestSilentStates:
    def test_human_paused_silent(self):
        assert is_silent_state(State.HUMAN_PAUSED)

    def test_closed_silent(self):
        assert is_silent_state(State.CLOSED)

    def test_waiting_team_not_silent(self):
        # waiting_team allows 1-shot ack
        assert not is_silent_state(State.WAITING_TEAM)
