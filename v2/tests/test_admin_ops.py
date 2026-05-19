"""
v2/tests/test_admin_ops.py — Tests for DEV-2026-05-19-004 admin handoff foundation.

Coverage:

    - pause_bot_for_customer creates an active bot_pauses row + updates the
      conversation to human_paused with paused_until.
    - resume_bot_for_customer closes the active pause + moves conversation
      back to a non-silent state + closes any open handoff.
    - Paused state is detected by is_bot_paused_for AND surfaces as
      is_silent=True in the AdminCaseSummary.
    - The existing orchestrator pause-guard (`conv.is_human_paused`) remains
      truthy after pause and falsy after resume — so the bot stays silent.
    - AdminCaseSummary resolves: display name, masked PSID, conversation
      state, latest memory fields, selected tour, latest offer, open handoff.
    - list_admin_cases returns newest-first and respects only_open / only_paused.
    - list_open_handoffs returns masked PSIDs and never echoes raw secrets or
      wholesale tokens.
    - Hard rules: no secrets / no wholesale brand names introduced.
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from v2.lib import admin_ops
from v2.lib.admin_ops import (
    AdminCaseSummary,
    DEFAULT_PAUSE_TTL_MINUTES,
    MAX_PAUSE_TTL_MINUTES,
    get_admin_case,
    is_bot_paused_for,
    list_admin_cases,
    list_open_handoffs,
    pause_bot_for_customer,
    record_handoff,
    resume_bot_for_customer,
)
from v2.lib.memory import MemoryService, TourOption
from v2.lib.response_writer import _WHOLESALE_BLACKLIST


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PSID_A = "1234567890123456"
PSID_B = "9876543210987654"


def _seed_customer(supabase, psid: str, name: str = "Alice ลูกค้า") -> dict:
    cust = supabase.table("customers").insert({"psid": psid, "fb_name": name})
    return cust


def _seed_open_conversation(supabase, customer_id: str, psid: str,
                            state: str = "collecting_preferences",
                            last_activity_at: str | None = None) -> dict:
    return supabase.table("conversations").insert({
        "customer_id": customer_id,
        "psid": psid,
        "state": state,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "last_activity_at": last_activity_at or datetime.now(timezone.utc).isoformat(),
        "closed_at": None,
        "is_human_paused": False,
    })


# ---------------------------------------------------------------------------
# Pause
# ---------------------------------------------------------------------------

class TestPause:
    def test_pause_creates_bot_pauses_row_and_updates_conversation(self, supabase):
        cust = _seed_customer(supabase, PSID_A)
        _seed_open_conversation(supabase, cust["id"], PSID_A)

        result = pause_bot_for_customer(
            supabase, psid=PSID_A, paused_by="admin", reason="taking over"
        )

        assert result.psid == PSID_A
        assert result.paused_by == "admin"
        assert result.pause_until > datetime.now(timezone.utc).isoformat()

        # bot_pauses row
        pause_row = supabase.table("bot_pauses").select_one(
            {"psid": PSID_A, "resumed_at": None}
        )
        assert pause_row is not None
        assert pause_row["paused_by"] == "admin"
        assert pause_row["reason"] == "taking over"
        assert pause_row["resumed_at"] is None
        assert pause_row["pause_until"] > pause_row["paused_at"]

        # Conversation row: is_human_paused and state human_paused
        conv = supabase.table("conversations").select_one(
            {"psid": PSID_A, "closed_at": None}
        )
        assert conv["is_human_paused"] is True
        assert conv["paused_until"] == pause_row["pause_until"]
        assert conv["paused_reason"] == "taking over"
        assert conv["state"] == "human_paused"

    def test_pause_records_handoff_when_none_open(self, supabase):
        cust = _seed_customer(supabase, PSID_A)
        _seed_open_conversation(supabase, cust["id"], PSID_A)

        result = pause_bot_for_customer(
            supabase, psid=PSID_A, paused_by="admin",
            reason="follow up", handoff_trigger_type="human_request",
        )

        assert result.handoff_id is not None
        handoff = supabase.table("handoffs").select_one({"id": result.handoff_id})
        assert handoff["trigger_type"] == "human_request"
        assert handoff["resolution"] is None
        assert handoff["psid"] == PSID_A

    def test_pause_reuses_existing_open_handoff(self, supabase):
        cust = _seed_customer(supabase, PSID_A)
        conv = _seed_open_conversation(supabase, cust["id"], PSID_A)
        existing_h = supabase.table("handoffs").insert({
            "conversation_id": conv["id"],
            "psid": PSID_A,
            "triggered_at": datetime.now(timezone.utc).isoformat(),
            "trigger_type": "attachment",
            "trigger_detail": {},
            "resolution": None,
        })

        result = pause_bot_for_customer(
            supabase, psid=PSID_A, paused_by="admin", reason="image slip",
        )

        # Same row reused, no second handoff inserted
        all_handoffs = supabase.table("handoffs").select_all({"psid": PSID_A})
        assert len(all_handoffs) == 1
        assert result.handoff_id == existing_h["id"]

    def test_pause_validates_inputs(self, supabase):
        _seed_customer(supabase, PSID_A)
        with pytest.raises(ValueError):
            pause_bot_for_customer(supabase, psid="", paused_by="admin")
        with pytest.raises(ValueError):
            pause_bot_for_customer(supabase, psid=PSID_A, paused_by="not_allowed")
        with pytest.raises(ValueError):
            pause_bot_for_customer(supabase, psid=PSID_A, paused_by="admin",
                                   ttl_minutes=0)
        with pytest.raises(ValueError):
            pause_bot_for_customer(supabase, psid=PSID_A, paused_by="admin",
                                   ttl_minutes=MAX_PAUSE_TTL_MINUTES + 1)

    def test_pause_without_active_conversation_still_inserts_pause(self, supabase):
        _seed_customer(supabase, PSID_A)
        # No conversation row yet
        result = pause_bot_for_customer(
            supabase, psid=PSID_A, paused_by="system", reason="pre-emptive",
        )
        assert result.conversation_id is None
        # bot_pauses row exists with conversation_id=None
        pause = supabase.table("bot_pauses").select_one({"id": result.pause_id})
        assert pause is not None
        assert pause["conversation_id"] is None
        # No handoff (no conversation to attach to)
        assert result.handoff_id is None

    def test_paused_default_ttl_uses_120_minutes(self, supabase):
        cust = _seed_customer(supabase, PSID_A)
        _seed_open_conversation(supabase, cust["id"], PSID_A)

        result = pause_bot_for_customer(
            supabase, psid=PSID_A, paused_by="admin"
        )
        pause_until = datetime.fromisoformat(result.pause_until.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta_min = (pause_until - now).total_seconds() / 60.0
        # Allow ±2 min slack for test timing
        assert DEFAULT_PAUSE_TTL_MINUTES - 2 <= delta_min <= DEFAULT_PAUSE_TTL_MINUTES + 2


# ---------------------------------------------------------------------------
# Silent path — orchestrator-compatible guard
# ---------------------------------------------------------------------------

class TestSilentPath:
    def test_is_bot_paused_for_true_after_pause(self, supabase):
        cust = _seed_customer(supabase, PSID_A)
        _seed_open_conversation(supabase, cust["id"], PSID_A)

        assert is_bot_paused_for(supabase, PSID_A) is False
        pause_bot_for_customer(supabase, psid=PSID_A, paused_by="admin")
        assert is_bot_paused_for(supabase, PSID_A) is True

    def test_orchestrator_compatible_flag_is_set(self, supabase):
        """
        The existing orchestrator short-circuit in v2/lib/orchestrator.py reads
        `conv.get('is_human_paused')` and returns silent_paused. This test
        guards that contract: after admin_ops pause, the conversation row has
        that flag truthy (so the orchestrator stays silent).
        """
        cust = _seed_customer(supabase, PSID_A)
        _seed_open_conversation(supabase, cust["id"], PSID_A)

        pause_bot_for_customer(supabase, psid=PSID_A, paused_by="admin")

        conv = supabase.table("conversations").select_one(
            {"psid": PSID_A, "closed_at": None}
        )
        assert conv["is_human_paused"] is True
        assert conv["state"] == "human_paused"

        # Re-confirm using the public helper too
        assert is_bot_paused_for(supabase, PSID_A) is True


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

class TestResume:
    def test_resume_closes_active_pause_and_clears_conversation_flag(self, supabase):
        cust = _seed_customer(supabase, PSID_A)
        _seed_open_conversation(supabase, cust["id"], PSID_A)
        pause_bot_for_customer(supabase, psid=PSID_A, paused_by="admin",
                               reason="taking over")

        # Sleep one ms-worth — ISO strings monotonic per call.
        time.sleep(0.001)

        result = resume_bot_for_customer(
            supabase, psid=PSID_A, resumed_by="admin",
            reason="customer reverted to bot",
        )

        assert result.was_paused is True
        assert result.pause_id is not None
        assert result.resumed_at is not None

        pause = supabase.table("bot_pauses").select_one(
            {"psid": PSID_A, "resumed_at": None}
        )
        assert pause is None  # no longer active

        any_pause = supabase.table("bot_pauses").select_all({"psid": PSID_A})
        assert len(any_pause) == 1
        assert any_pause[0]["resumed_at"] is not None
        assert any_pause[0]["resumed_by"] == "admin"

        conv = supabase.table("conversations").select_one(
            {"psid": PSID_A, "closed_at": None}
        )
        assert conv["is_human_paused"] is False
        assert conv["paused_until"] is None
        assert conv["paused_reason"] is None
        # default returns to collecting_preferences — non-silent
        assert conv["state"] == "collecting_preferences"
        assert is_bot_paused_for(supabase, PSID_A) is False

    def test_resume_closes_open_handoffs(self, supabase):
        cust = _seed_customer(supabase, PSID_A)
        _seed_open_conversation(supabase, cust["id"], PSID_A)
        pause_bot_for_customer(supabase, psid=PSID_A, paused_by="admin",
                               reason="image slip",
                               handoff_trigger_type="attachment")

        result = resume_bot_for_customer(
            supabase, psid=PSID_A, resumed_by="admin", reason="resolved"
        )
        assert result.handoffs_closed == 1

        handoffs = supabase.table("handoffs").select_all({"psid": PSID_A})
        assert len(handoffs) == 1
        h = handoffs[0]
        assert h["resolution"] == "bot_resumed"
        assert h["resolution_at"] is not None
        assert h["admin_responder"] == "admin"

    def test_resume_into_silent_state_rejected(self, supabase):
        cust = _seed_customer(supabase, PSID_A)
        _seed_open_conversation(supabase, cust["id"], PSID_A)
        pause_bot_for_customer(supabase, psid=PSID_A, paused_by="admin")

        with pytest.raises(ValueError):
            resume_bot_for_customer(
                supabase, psid=PSID_A, resumed_by="admin",
                new_state="human_paused",
            )
        with pytest.raises(ValueError):
            resume_bot_for_customer(
                supabase, psid=PSID_A, resumed_by="admin",
                new_state="closed",
            )
        with pytest.raises(ValueError):
            resume_bot_for_customer(
                supabase, psid=PSID_A, resumed_by="admin",
                new_state="not_a_real_state",
            )

    def test_resume_when_not_paused_is_safe_noop_on_pause_row(self, supabase):
        cust = _seed_customer(supabase, PSID_A)
        _seed_open_conversation(supabase, cust["id"], PSID_A)

        result = resume_bot_for_customer(
            supabase, psid=PSID_A, resumed_by="admin",
        )
        assert result.was_paused is False
        assert result.pause_id is None
        assert result.resumed_at is None
        # Conversation state is still set to the requested non-silent state
        conv = supabase.table("conversations").select_one(
            {"psid": PSID_A, "closed_at": None}
        )
        assert conv["state"] == "collecting_preferences"


# ---------------------------------------------------------------------------
# Case summary
# ---------------------------------------------------------------------------

class TestCaseSummary:
    def test_summary_resolves_display_name_from_customer_memory_first(self, supabase, redis):
        cust = _seed_customer(supabase, PSID_A, name="FB Alice")
        _seed_open_conversation(supabase, cust["id"], PSID_A)
        mem = MemoryService(supabase, redis)
        mem.update_customer_memory(
            PSID_A,
            {"customer_name": "พี่อลิซ", "latest_country": "ญี่ปุ่น",
             "budget_per_person": 30000, "pax_count": 2,
             "travel_month": "2026-08", "airline_preference": "JL"},
            reason="test_setup",
        )

        summary = get_admin_case(supabase, psid=PSID_A, memory=mem)
        assert summary is not None
        assert summary.display_name == "พี่อลิซ"
        assert summary.latest_country == "ญี่ปุ่น"
        assert summary.budget_per_person == 30000
        assert summary.pax_count == 2
        assert summary.travel_month == "2026-08"
        assert summary.airline_preference == "JL"

    def test_summary_falls_back_to_fb_name_when_no_memory_name(self, supabase):
        cust = _seed_customer(supabase, PSID_A, name="FB Alice")
        _seed_open_conversation(supabase, cust["id"], PSID_A)
        summary = get_admin_case(supabase, psid=PSID_A)
        assert summary is not None
        assert summary.display_name == "FB Alice"

    def test_summary_masks_psid_in_visible_fields(self, supabase):
        _seed_customer(supabase, PSID_A)
        summary = get_admin_case(supabase, psid=PSID_A)
        assert summary is not None
        assert summary.psid == PSID_A  # raw still available for admin
        assert summary.psid_masked.startswith("1234")
        assert summary.psid_masked.endswith("56")
        assert "*" in summary.psid_masked

    def test_summary_includes_selected_tour(self, supabase, redis, make_tour):
        cust = _seed_customer(supabase, PSID_A)
        conv = _seed_open_conversation(supabase, cust["id"], PSID_A)
        mem = MemoryService(supabase, redis)
        # Pre-populate customer to satisfy lock_selected_tour preconditions
        mem.update_customer_memory(PSID_A, {"fb_name": "Test"}, "setup")

        tour = make_tour(
            web_code="ap999111", name="ทัวร์โตเกียว 5 วัน 4 คืน",
            price=18999, days=5, airline="HU",
            tour_code_real="BCCKG27-HU",
        )
        mem.lock_selected_tour(PSID_A, tour, conversation_id=conv["id"])

        summary = get_admin_case(supabase, psid=PSID_A, memory=mem)
        assert summary.selected_tour is not None
        assert summary.selected_tour.web_code == "ap999111"
        assert "โตเกียว" in (summary.selected_tour.name or "")
        assert summary.selected_tour.price == 18999

    def test_summary_includes_latest_offer(self, supabase, redis):
        cust = _seed_customer(supabase, PSID_A)
        conv = _seed_open_conversation(supabase, cust["id"], PSID_A)
        mem = MemoryService(supabase, redis)
        mem.save_offer_snapshot(
            PSID_A,
            [TourOption(rank=1, web_code="ap111111", tour_code_real="BCCKG27-HU",
                        name="ทัวร์โตเกียว 5 วัน", price=18999, days=5, airline="HU")],
            search_context={"country": "ญี่ปุ่น"},
            conversation_id=conv["id"],
        )

        summary = get_admin_case(supabase, psid=PSID_A, memory=mem)
        assert summary.latest_offer is not None
        assert summary.latest_offer.tour_count == 1
        assert summary.latest_offer.top_tour_web_code == "ap111111"
        assert summary.latest_offer.top_tour_price == 18999

    def test_summary_includes_open_handoff(self, supabase):
        cust = _seed_customer(supabase, PSID_A)
        conv = _seed_open_conversation(supabase, cust["id"], PSID_A)
        record_handoff(
            supabase, psid=PSID_A, trigger_type="fee_missing",
            trigger_detail={"missing_field": "single_supplement"},
            conversation_id=conv["id"],
        )
        summary = get_admin_case(supabase, psid=PSID_A)
        assert summary.open_handoff is not None
        assert summary.open_handoff.trigger_type == "fee_missing"
        assert "single_supplement" in (summary.open_handoff.trigger_detail_summary or "")
        # PSID in the open_handoff projection is masked
        assert summary.open_handoff.psid_masked != PSID_A

    def test_summary_is_silent_when_paused(self, supabase):
        cust = _seed_customer(supabase, PSID_A)
        _seed_open_conversation(supabase, cust["id"], PSID_A)
        pause_bot_for_customer(supabase, psid=PSID_A, paused_by="admin")

        summary = get_admin_case(supabase, psid=PSID_A)
        assert summary.is_paused is True
        assert summary.is_silent is True
        assert summary.conversation_state == "human_paused"

    def test_summary_unknown_psid_returns_none(self, supabase):
        # No customer row anywhere
        summary = get_admin_case(supabase, psid="NEVER_SEEN_PSID")
        assert summary is None

    def test_summary_by_conversation_id(self, supabase):
        cust = _seed_customer(supabase, PSID_A)
        conv = _seed_open_conversation(supabase, cust["id"], PSID_A)
        summary = get_admin_case(supabase, conversation_id=conv["id"])
        assert summary is not None
        assert summary.psid == PSID_A

    def test_summary_requires_at_least_one_id(self, supabase):
        with pytest.raises(ValueError):
            get_admin_case(supabase)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

class TestListings:
    def test_list_admin_cases_newest_first(self, supabase):
        cust_a = _seed_customer(supabase, PSID_A, name="Alice")
        cust_b = _seed_customer(supabase, PSID_B, name="Bob")
        _seed_open_conversation(supabase, cust_a["id"], PSID_A,
                                last_activity_at="2026-05-18T10:00:00+00:00")
        _seed_open_conversation(supabase, cust_b["id"], PSID_B,
                                last_activity_at="2026-05-19T09:00:00+00:00")

        cases = list_admin_cases(supabase)
        assert [c.psid for c in cases] == [PSID_B, PSID_A]

    def test_list_admin_cases_only_paused(self, supabase):
        cust_a = _seed_customer(supabase, PSID_A)
        cust_b = _seed_customer(supabase, PSID_B)
        _seed_open_conversation(supabase, cust_a["id"], PSID_A)
        _seed_open_conversation(supabase, cust_b["id"], PSID_B)
        pause_bot_for_customer(supabase, psid=PSID_B, paused_by="admin")

        all_cases = list_admin_cases(supabase)
        assert len(all_cases) == 2
        paused_only = list_admin_cases(supabase, only_paused=True)
        assert len(paused_only) == 1
        assert paused_only[0].psid == PSID_B

    def test_list_admin_cases_only_open_excludes_closed(self, supabase):
        cust_a = _seed_customer(supabase, PSID_A)
        cust_b = _seed_customer(supabase, PSID_B)
        _seed_open_conversation(supabase, cust_a["id"], PSID_A)
        closed = _seed_open_conversation(supabase, cust_b["id"], PSID_B)
        supabase.table("conversations").update(
            {"id": closed["id"]},
            {"closed_at": datetime.now(timezone.utc).isoformat()},
        )

        cases = list_admin_cases(supabase, only_open=True)
        assert {c.psid for c in cases} == {PSID_A}

    def test_list_open_handoffs_masks_psid_and_redacts_wholesale(self, supabase):
        cust = _seed_customer(supabase, PSID_A)
        conv = _seed_open_conversation(supabase, cust["id"], PSID_A)
        record_handoff(
            supabase, psid=PSID_A, trigger_type="human_request",
            trigger_detail={"reason": "TTN partner asked for follow-up"},
            conversation_id=conv["id"],
        )
        # Closed handoff for the same psid — must not appear
        resolved = supabase.table("handoffs").insert({
            "conversation_id": conv["id"], "psid": PSID_A,
            "triggered_at": datetime.now(timezone.utc).isoformat(),
            "trigger_type": "fee_missing", "trigger_detail": {},
            "resolution": "booked", "resolution_at": datetime.now(timezone.utc).isoformat(),
        })

        opens = list_open_handoffs(supabase)
        assert len(opens) == 1
        h = opens[0]
        assert h.psid_masked != PSID_A
        assert "*" in h.psid_masked
        # wholesale token redacted
        assert "TTN" not in (h.trigger_detail_summary or "")
        assert "***WHOLESALE-REDACTED***" in (h.trigger_detail_summary or "")
        # resolved handoff not present
        assert h.id != resolved["id"]


# ---------------------------------------------------------------------------
# Hard rule guards
# ---------------------------------------------------------------------------

class TestNoSecretOrWholesaleLeakage:
    def test_no_secret_pattern_appears_in_module(self):
        """Source file must not contain any secret-looking literal."""
        src = open("v2/lib/admin_ops.py", "r", encoding="utf-8").read()
        # OpenAI / Anthropic-shaped strings (with realistic length thresholds)
        assert not re.search(r"sk-[A-Za-z0-9_-]{20,}", src)
        # FB page tokens
        assert not re.search(r"EAA[A-Za-z0-9_]{30,}", src)
        # GitHub PAT shape
        assert not re.search(r"ghp_[A-Za-z0-9]{20,}", src)

    def test_no_wholesale_brand_token_appears_in_module(self):
        """Source file must not contain wholesale partner tokens in plain code paths."""
        src = open("v2/lib/admin_ops.py", "r", encoding="utf-8").read()
        for pat in _WHOLESALE_BLACKLIST:
            assert not pat.search(src), \
                f"Wholesale token leaked in admin_ops.py source: {pat.pattern}"

    def test_summary_redacts_wholesale_in_tour_name(self, supabase, redis, make_tour):
        cust = _seed_customer(supabase, PSID_A)
        conv = _seed_open_conversation(supabase, cust["id"], PSID_A)
        mem = MemoryService(supabase, redis)
        mem.update_customer_memory(PSID_A, {"fb_name": "Test"}, "setup")

        # Hostile case: scraper accidentally captured a wholesale token in the
        # tour name. The admin view must redact it before showing to admin
        # (defense in depth — `tours_canonical.name` is also reviewed
        # independently by the scraper test surface).
        tour = make_tour(
            web_code="ap000001",
            name="ทัวร์โตเกียว by TTN partner",  # hostile string
            price=18999,
        )
        mem.lock_selected_tour(PSID_A, tour, conversation_id=conv["id"])
        summary = get_admin_case(supabase, psid=PSID_A, memory=mem)
        assert "TTN" not in (summary.selected_tour.name or "")
        assert "***WHOLESALE-REDACTED***" in (summary.selected_tour.name or "")

    def test_summary_redacts_wholesale_in_display_name(self, supabase):
        # Hostile fb_name with a wholesale token — must not leak to admin view.
        supabase.table("customers").insert({
            "psid": PSID_A, "fb_name": "Alice via ZEGO promo",
        })
        _seed_open_conversation(
            supabase,
            supabase.table("customers").select_one({"psid": PSID_A})["id"],
            PSID_A,
        )
        summary = get_admin_case(supabase, psid=PSID_A)
        assert "ZEGO" not in (summary.display_name or "")
        assert "***WHOLESALE-REDACTED***" in (summary.display_name or "")


# ---------------------------------------------------------------------------
# record_handoff direct surface
# ---------------------------------------------------------------------------

class TestRecordHandoff:
    def test_record_handoff_requires_conversation(self, supabase):
        # No conversation, no conversation_id passed
        _seed_customer(supabase, PSID_A)
        with pytest.raises(ValueError):
            record_handoff(supabase, psid=PSID_A, trigger_type="fee_missing")

    def test_record_handoff_validates_trigger_type(self, supabase):
        cust = _seed_customer(supabase, PSID_A)
        conv = _seed_open_conversation(supabase, cust["id"], PSID_A)
        with pytest.raises(ValueError):
            record_handoff(
                supabase, psid=PSID_A, trigger_type="not_in_check_list",
                conversation_id=conv["id"],
            )

    def test_record_handoff_inserts_row(self, supabase):
        cust = _seed_customer(supabase, PSID_A)
        conv = _seed_open_conversation(supabase, cust["id"], PSID_A)
        hid = record_handoff(
            supabase, psid=PSID_A, trigger_type="fee_missing",
            trigger_detail={"missing_field": "single_supplement"},
            conversation_id=conv["id"],
        )
        row = supabase.table("handoffs").select_one({"id": hid})
        assert row["trigger_type"] == "fee_missing"
        assert row["trigger_detail"]["missing_field"] == "single_supplement"
        assert row["resolution"] is None
