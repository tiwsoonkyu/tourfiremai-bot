from __future__ import annotations

import re
from datetime import datetime, timezone

from v2.lib.admin_command_handler import (
    handle_admin_command,
    parse_admin_command,
)
from v2.lib.admin_ops import pause_bot_for_customer, record_handoff


PSID_A = "1234567890123456"
PSID_B = "9876543210987654"


def _seed_customer(supabase, psid: str = PSID_A, name: str = "Supakit Test") -> dict:
    return supabase.table("customers").insert({"psid": psid, "fb_name": name})


def _seed_open_conversation(supabase, customer_id: str, psid: str = PSID_A,
                            state: str = "collecting_preferences") -> dict:
    return supabase.table("conversations").insert({
        "customer_id": customer_id,
        "psid": psid,
        "state": state,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "last_activity_at": datetime.now(timezone.utc).isoformat(),
        "closed_at": None,
        "is_human_paused": False,
    })


def _seed_case_with_tour(supabase, psid: str = PSID_A) -> dict:
    cust = _seed_customer(supabase, psid=psid)
    conv = _seed_open_conversation(supabase, cust["id"], psid=psid)
    tour = supabase.table("tours_canonical").insert({
        "web_code": "ap111111",
        "tour_code_real": "REAL-TOUR-001",
        "name": "Tokyo Free Day",
        "base_price": 19999,
    })
    supabase.table("selected_tours").insert({
        "psid": psid,
        "tour_id": tour["id"],
        "tour_code_real": "REAL-TOUR-001",
        "selected_at": datetime.now(timezone.utc).isoformat(),
        "unlocked_at": None,
        "booking_status": "interested",
        "is_fee_acknowledged": False,
    })
    supabase.table("offer_snapshots").insert({
        "psid": psid,
        "presented_at": datetime.now(timezone.utc).isoformat(),
        "tour_list": [
            {"web_code": "ap111111", "name": "Tokyo Free Day", "price": 19999}
        ],
        "was_selected": True,
        "selected_rank": 1,
    })
    return conv


class TestParseAdminCommand:
    def test_parse_supported_commands(self):
        cases = {
            "cases": ("cases", None, None),
            "cases paused": ("cases_paused", None, None),
            "handoffs": ("handoffs", None, None),
            f"case {PSID_A}": ("case", PSID_A, None),
            f"pause {PSID_A} admin takeover": ("pause", PSID_A, "admin takeover"),
            f"resume {PSID_A} done": ("resume", PSID_A, "done"),
            "help": ("help", None, None),
        }
        for text, expected in cases.items():
            cmd = parse_admin_command(text)
            assert (cmd.action, cmd.target, cmd.reason) == expected

    def test_parse_whitespace_and_unknown_safely(self):
        assert parse_admin_command("  pause   1234567890123456   ดูเอง  ").reason == "ดูเอง"
        unknown = parse_admin_command("รับเคส 123")
        assert unknown.action == "unknown"
        assert unknown.target is None


class TestReadCommands:
    def test_cases_lists_safe_admin_lines(self, supabase):
        _seed_case_with_tour(supabase, PSID_A)

        result = handle_admin_command("cases", supabase, admin_user_id="line-admin")

        assert result.ok is True
        assert result.action == "cases"
        assert "เคสล่าสุด" in result.admin_text
        assert "Supakit Test" in result.admin_text
        assert PSID_A not in result.admin_text
        assert "1234" in result.admin_text

    def test_handoffs_lists_open_handoffs(self, supabase):
        cust = _seed_customer(supabase, PSID_A)
        _seed_open_conversation(supabase, cust["id"], PSID_A)
        record_handoff(
            supabase,
            psid=PSID_A,
            trigger_type="human_request",
            trigger_detail={"reason": "customer asks for staff"},
        )

        result = handle_admin_command("handoffs", supabase, admin_user_id="line-admin")

        assert result.ok is True
        assert "Handoffs" in result.admin_text
        assert "human_request" in result.admin_text
        assert PSID_A not in result.admin_text

    def test_case_detail_includes_context(self, supabase):
        conv = _seed_case_with_tour(supabase, PSID_A)

        result = handle_admin_command(
            f"case {conv['id']}", supabase, admin_user_id="line-admin"
        )

        assert result.ok is True
        assert "เคส: Supakit Test" in result.admin_text
        assert "Tokyo Free Day" in result.admin_text
        assert "REAL-TOUR-001" in result.admin_text
        assert PSID_A not in result.admin_text


class TestMutatingCommands:
    def test_pause_calls_admin_ops_and_marks_paused(self, supabase):
        _seed_case_with_tour(supabase, PSID_A)

        result = handle_admin_command(
            f"pause {PSID_A} admin takeover", supabase, admin_user_id="line-admin"
        )

        assert result.ok is True
        assert result.mutated is True
        assert "หยุดบอท" in result.admin_text
        conv = supabase.table("conversations").select_one({"psid": PSID_A, "closed_at": None})
        assert conv["is_human_paused"] is True
        assert conv["state"] == "human_paused"

    def test_resume_calls_admin_ops_and_clears_paused(self, supabase):
        _seed_case_with_tour(supabase, PSID_A)
        pause_bot_for_customer(supabase, psid=PSID_A, paused_by="admin")

        result = handle_admin_command(
            f"resume {PSID_A} done", supabase, admin_user_id="line-admin"
        )

        assert result.ok is True
        assert result.mutated is True
        assert "เปิดบอทกลับ" in result.admin_text
        conv = supabase.table("conversations").select_one({"psid": PSID_A, "closed_at": None})
        assert conv["is_human_paused"] is False
        assert conv["state"] == "collecting_preferences"

    def test_missing_target_does_not_create_pause(self, supabase):
        result = handle_admin_command(
            f"pause {PSID_B} missing", supabase, admin_user_id="line-admin"
        )

        assert result.ok is False
        assert result.error == "case_not_found"
        assert "ยังไม่ได้หยุดบอท" in result.admin_text
        assert supabase.table("bot_pauses").select_all({}) == []


class TestLeakageSafety:
    def test_unknown_command_returns_help_and_does_not_mutate(self, supabase):
        result = handle_admin_command("รับเคสอะไรสักอย่าง", supabase, admin_user_id="line-admin")

        assert result.ok is False
        assert result.error == "unknown_command"
        assert "คำสั่งแอดมิน" in result.admin_text
        assert result.mutated is False
        assert supabase.table("bot_pauses").select_all({}) == []

    def test_output_redacts_secret_patterns(self, supabase):
        fake_key = f"s{'k'}-proj-THIS_IS_A_FAKE_TEST_KEY_1234567890"
        cust = _seed_customer(supabase, PSID_A, name=fake_key)
        _seed_open_conversation(supabase, cust["id"], PSID_A)

        result = handle_admin_command("cases", supabase, admin_user_id="line-admin")

        assert "FAKE_TEST_KEY" not in result.admin_text
        assert f"s{'k'}-" not in result.admin_text

    def test_output_redacts_configured_provider_names(self, supabase, monkeypatch):
        import v2.lib.admin_command_handler as handler

        monkeypatch.setattr(
            handler,
            "_WHOLESALE_BLACKLIST",
            [re.compile(r"forbidden-provider", re.I)],
        )
        cust = _seed_customer(supabase, PSID_A, name="Safe Customer")
        conv = _seed_open_conversation(supabase, cust["id"], PSID_A)
        tour = supabase.table("tours_canonical").insert({
            "web_code": "ap222222",
            "tour_code_real": "SAFE-001",
            "name": "forbidden-provider Tokyo",
            "base_price": 20000,
        })
        supabase.table("selected_tours").insert({
            "psid": PSID_A,
            "tour_id": tour["id"],
            "selected_at": datetime.now(timezone.utc).isoformat(),
            "unlocked_at": None,
        })

        result = handle_admin_command(
            f"case {conv['id']}", supabase, admin_user_id="line-admin"
        )

        assert result.ok is True
        assert "forbidden-provider" not in result.admin_text.lower()
        assert "***WHOLESALE-REDACTED***" in result.admin_text
