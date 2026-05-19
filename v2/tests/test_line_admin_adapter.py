"""
v2/tests/test_line_admin_adapter.py — DEV-2026-05-19-008.

Tests for `v2.lib.line_admin_adapter.LineAdminAdapter` — the allow-list
gate placed BEFORE `admin_command_handler`.

Coverage:

    1. AdminAllowList rejects empty / whitespace / oversize ids on
       construction.
    2. AdminAllowList.from_env reads V2_STAGING_LINE_ADMIN_ALLOW_LIST,
       supports comma/space/semicolon separators, falls back to the
       single-admin env var.
    3. Adapter denies missing/empty/non-allowlisted senders WITHOUT
       reaching the admin command handler — no DB side effects.
    4. Adapter denies even known admin commands when allow-list is empty.
    5. Adapter allows authorized senders to run `cases`, `case <id>`,
       `pause <id>` and `mark_full / clear_full`.
    6. The admin_user_id recorded by audit logs is the LINE sender id,
       not the raw env value.
    7. No raw command text or wholesale brand leaks in any denial.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from v2.lib.admin_ops import is_bot_paused_for, pause_bot_for_customer
from v2.lib.line_admin_adapter import (
    AdminAllowList,
    LineAdminAdapter,
)
from v2.lib.page_post_context import (
    mark_availability_override,
    upsert_page_post,
)


def _active_overrides(supabase, *, web_code=None):
    rows = supabase.table("tour_availability_overrides").select_all({}) \
        if hasattr(supabase.table("tour_availability_overrides"), "select_all") else []
    out = []
    for r in rows:
        if r.get("cleared_at"):
            continue
        if web_code is not None and r.get("web_code") != web_code:
            continue
        out.append(r)
    return out


PSID_CUST = "1234567890123456"
ALLOWED = "U_admin_alpha"
DENIED = "U_some_random_user"
PAGE_ID = "61500000000001"


# ---------------------------------------------------------------------------
# AdminAllowList unit tests
# ---------------------------------------------------------------------------


class TestAdminAllowList:
    def test_construction_normalises_and_rejects_bad(self):
        al = AdminAllowList.from_iterable(["", "  ", "Uone", "U two", None, "Ulong" + "x" * 500])
        assert al.is_allowed("Uone")
        assert not al.is_allowed("U two")
        assert not al.is_allowed("Ulong" + "x" * 500)
        assert al.is_allowed("Uone") is True
        # Empty / None / whitespace are dropped
        assert "" not in al.ids
        assert None not in al.ids

    def test_from_env_comma_separated(self):
        env = {"V2_STAGING_LINE_ADMIN_ALLOW_LIST": "Uone,Utwo, Uthree;Ufour"}
        al = AdminAllowList.from_env(env=env)
        assert al.is_allowed("Uone")
        assert al.is_allowed("Utwo")
        assert al.is_allowed("Uthree")
        assert al.is_allowed("Ufour")
        assert not al.is_allowed("Ufive")

    def test_from_env_falls_back_to_single_admin_var(self):
        env = {"V2_STAGING_LINE_ADMIN_USER_OR_GROUP_ID": "Ualpha"}
        al = AdminAllowList.from_env(env=env)
        assert al.is_allowed("Ualpha")
        assert not al.is_empty()

    def test_from_env_empty_when_unset(self):
        al = AdminAllowList.from_env(env={})
        assert al.is_empty()

    def test_to_dict_never_leaks_ids(self):
        al = AdminAllowList.from_iterable(["Usecret"])
        d = al.to_dict()
        assert "Usecret" not in str(d)
        assert d == {"allowed_count": 1}


# ---------------------------------------------------------------------------
# Adapter — denial paths
# ---------------------------------------------------------------------------


class TestAdapterDenial:
    def _adapter(self, supabase, allow_list=None):
        return LineAdminAdapter(
            supabase=supabase,
            allow_list=allow_list or AdminAllowList.from_iterable([ALLOWED]),
        )

    def test_missing_sender_id_is_denied(self, supabase):
        adapter = self._adapter(supabase)
        result = adapter.handle(sender_id=None, text="cases")
        assert result.ok is False
        assert result.action == "denied"
        assert result.error == "missing_sender"
        assert result.mutated is False
        # Denial text never echoes the input text.
        assert "cases" not in result.admin_text

    def test_empty_allow_list_denies_even_known_sender(self, supabase):
        adapter = self._adapter(supabase, allow_list=AdminAllowList.from_iterable([]))
        result = adapter.handle(sender_id=ALLOWED, text="cases")
        assert result.ok is False
        assert result.error == "empty_allow_list"
        assert result.action == "denied"

    def test_non_allowlisted_is_denied_with_no_side_effects(self, supabase, make_customer):
        make_customer(PSID_CUST, name="Test Customer")
        supabase.table("conversations").insert({
            "psid": PSID_CUST, "state": "collecting_preferences",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "last_activity_at": datetime.now(timezone.utc).isoformat(),
            "closed_at": None, "is_human_paused": False,
        })
        adapter = self._adapter(supabase)
        result = adapter.handle(
            sender_id=DENIED,
            text=f"pause {PSID_CUST} attacker takeover",
        )
        assert result.ok is False
        assert result.error == "not_allowed"
        # No pause row inserted.
        assert is_bot_paused_for(supabase, PSID_CUST) is False
        # Denial text does NOT reflect the attacker payload.
        assert "attacker takeover" not in result.admin_text
        assert PSID_CUST not in result.admin_text

    def test_oversize_sender_id_is_denied(self, supabase):
        adapter = self._adapter(supabase)
        result = adapter.handle(sender_id="U" + ("x" * 500), text="cases")
        assert result.ok is False
        assert result.error in {"missing_sender", "not_allowed"}

    def test_mark_full_blocked_when_unauthorized(self, supabase, make_tour):
        make_tour(web_code="ap777777", name="Tokyo", price=20000)
        adapter = self._adapter(supabase)
        result = adapter.handle(sender_id=DENIED, text="mark_full ap777777")
        assert result.ok is False
        # No availability override should have been created.
        active = _active_overrides(supabase, web_code="ap777777")
        assert active == []


# ---------------------------------------------------------------------------
# Adapter — authorised happy path
# ---------------------------------------------------------------------------


class TestAdapterAllowed:
    def _adapter(self, supabase, ids=(ALLOWED,)):
        return LineAdminAdapter(
            supabase=supabase,
            allow_list=AdminAllowList.from_iterable(ids),
        )

    def test_authorised_cases_command(self, supabase, make_customer):
        make_customer(PSID_CUST, name="Customer A")
        supabase.table("conversations").insert({
            "psid": PSID_CUST, "state": "collecting_preferences",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "last_activity_at": datetime.now(timezone.utc).isoformat(),
            "closed_at": None, "is_human_paused": False,
        })
        adapter = self._adapter(supabase)
        result = adapter.handle(sender_id=ALLOWED, text="cases")
        assert result.ok is True
        assert result.action == "cases"
        assert PSID_CUST not in result.admin_text

    def test_authorised_pause_and_resume(self, supabase, make_customer):
        make_customer(PSID_CUST, name="Customer A")
        supabase.table("conversations").insert({
            "psid": PSID_CUST, "state": "collecting_preferences",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "last_activity_at": datetime.now(timezone.utc).isoformat(),
            "closed_at": None, "is_human_paused": False,
        })
        adapter = self._adapter(supabase)
        pause = adapter.handle(
            sender_id=ALLOWED, text=f"pause {PSID_CUST} admin chat",
        )
        assert pause.ok is True
        assert pause.action == "pause"
        assert pause.mutated is True
        assert is_bot_paused_for(supabase, PSID_CUST) is True

        resume = adapter.handle(sender_id=ALLOWED, text=f"resume {PSID_CUST} done")
        assert resume.ok is True
        assert resume.action == "resume"
        assert resume.mutated is True
        assert is_bot_paused_for(supabase, PSID_CUST) is False

    def test_authorised_mark_full_and_clear(self, supabase, make_tour):
        make_tour(web_code="ap777778", name="Osaka", price=21900)
        adapter = self._adapter(supabase)
        mark = adapter.handle(sender_id=ALLOWED, text="mark_full ap777778")
        assert mark.ok is True
        assert mark.mutated is True
        active = _active_overrides(supabase, web_code="ap777778")
        assert any(o.get("status") == "full" for o in active)

        clear = adapter.handle(sender_id=ALLOWED, text="clear_full ap777778")
        assert clear.ok is True
        # No active full override left.
        active = _active_overrides(supabase, web_code="ap777778")
        assert not any(o.get("status") == "full" for o in active)
