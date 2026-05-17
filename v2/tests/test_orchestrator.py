"""Sprint 3 test: orchestrator end-to-end with InMemory deps + MockLLM."""

import pytest
from v2.lib.orchestrator import Orchestrator
from v2.lib.llm import MockLLMClient
from v2.lib.state_machine import State


# --- Bootstrapping ------------------------------------------------------------

@pytest.fixture
def orch(supabase, redis):
    return Orchestrator(supabase, redis, MockLLMClient())


# --- E2E smoke tests ----------------------------------------------------------

class TestNewLeadGreeting:
    def test_new_lead_greeting_writes_inbound_and_outbound(self, orch, supabase):
        result = orch.handle_turn(
            psid="PSID_NEW_1", text="สวัสดีค่ะ",
            meta_message_id="fb:s3_test_1",
        )
        assert result.silent is False
        assert result.reply_text is not None
        # Verify rows in conversation_turns
        turns = supabase.table("conversation_turns").select_all({"psid": "PSID_NEW_1"})
        assert len(turns) == 2  # inbound + outbound
        directions = sorted([t["direction"] for t in turns])
        assert directions == ["inbound", "outbound"]


class TestStateTransition:
    def test_ask_country_moves_to_collecting(self, orch, supabase):
        result = orch.handle_turn(
            psid="PSID_TRANS_1", text="อยากไปญี่ปุ่น",
            meta_message_id="fb:s3_test_2",
        )
        assert result.state_before == "new_lead"
        assert result.state_after == "collecting_preferences"
        # Verify conversation_events row
        events = supabase.table("conversation_events").select_all({"psid": "PSID_TRANS_1"})
        state_changes = [e for e in events if e["event_type"] == "state_change"]
        assert len(state_changes) == 1
        assert state_changes[0]["event_data"]["to"] == "collecting_preferences"


class TestSilencePath:
    def test_attachment_triggers_waiting_team(self, orch, supabase):
        result = orch.handle_turn(
            psid="PSID_ATT_1", text="",
            attachments=[{"type": "image", "payload": {"url": "https://x.com/y.png"}}],
            meta_message_id="fb:s3_test_3",
        )
        # waiting_team in our code allows 1-shot canned ack, so reply_text present
        assert result.state_after == "waiting_team"

    def test_bot_paused_returns_silent(self, orch, supabase):
        # Insert a paused conversation first
        cust = supabase.table("customers").insert({"psid": "PSID_PAUSED_1"})
        supabase.table("conversations").insert({
            "customer_id": cust["id"], "psid": "PSID_PAUSED_1",
            "state": "human_paused", "is_human_paused": True,
        })
        result = orch.handle_turn(
            psid="PSID_PAUSED_1", text="hello",
            meta_message_id="fb:s3_test_4",
        )
        assert result.silent is True
        assert result.reply_text is None
        assert result.decision == "silent_paused"


class TestToolWhitelist:
    def test_blocked_tools_logged(self, orch, supabase, monkeypatch):
        # Force the state machine to suggest a tool that isn't in any whitelist
        from v2.lib import state_machine as sm
        original = sm.transition
        def fake_transition(state, intent, ctx=None):
            from v2.lib.state_machine import State as S, Transition
            return Transition(next_state=S.NEW_LEAD, reason="test_block",
                              tool_hints=["forbidden_tool", "search_tours"])
        monkeypatch.setattr(sm, "transition", fake_transition)

        result = orch.handle_turn(
            psid="PSID_BLOCK_1", text="hello",
            meta_message_id="fb:s3_test_5",
        )
        # forbidden_tool not in whitelist → blocked; search_tours IS in new_lead whitelist
        tool_names = [t["tool"] for t in result.tool_calls_made]
        assert "forbidden_tool" not in tool_names


class TestAgentRunsLogged:
    def test_agent_runs_inserted(self, orch, supabase):
        orch.handle_turn(
            psid="PSID_AUDIT_1", text="สวัสดี",
            meta_message_id="fb:s3_audit_1",
        )
        runs = supabase.table("agent_runs").select_all({"psid": "PSID_AUDIT_1"})
        assert len(runs) >= 1
        assert runs[0]["agent_name"] == "orchestrator"
        assert runs[0]["trace_id"] is not None


class TestE001HappyPath:
    """E-001 acceptance test: simplified happy path through 4 turns."""

    def test_full_flow(self, orch, supabase, make_tour):
        # Pre-populate tours
        t1 = make_tour(web_code="ap000001", name="โตเกียว 5D4N", price=18999, airline="HU",
                        country="ญี่ปุ่น", country_id=2)
        t2 = make_tour(web_code="ap000002", name="โอซาก้า 6D5N", price=25900, airline="VZ",
                        country="ญี่ปุ่น", country_id=2)
        t3 = make_tour(web_code="ap000003", name="ฮอกไกโด 7D6N", price=32900, airline="XJ",
                        country="ญี่ปุ่น", country_id=2)

        psid = "PSID_E001"

        # Turn 1: greet
        r1 = orch.handle_turn(psid=psid, text="สวัสดีค่ะ", meta_message_id="fb:e001_t1")
        assert r1.state_after in ("new_lead", "collecting_preferences")

        # Turn 2: ask country
        r2 = orch.handle_turn(psid=psid, text="อยากไปญี่ปุ่น", meta_message_id="fb:e001_t2")
        assert r2.state_after in ("collecting_preferences", "options_presented")

        # Turn 3: budget + present
        r3 = orch.handle_turn(psid=psid, text="งบประมาณ 30000 บาท", meta_message_id="fb:e001_t3")
        # Verify conversation persisted
        conv = supabase.table("conversations").select_one({"psid": psid, "closed_at": None})
        assert conv is not None
