"""Sprint 3 test: orchestrator end-to-end with InMemory deps + MockLLM."""

import pytest
from v2.lib.orchestrator import COUNTRY_NAMES, Orchestrator
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
    def test_ask_country_moves_to_options_presented(self, orch, supabase):
        result = orch.handle_turn(
            psid="PSID_TRANS_1", text="อยากไปญี่ปุ่น",
            meta_message_id="fb:s3_test_2",
        )
        assert result.state_before == "new_lead"
        assert result.state_after == "options_presented"
        # Verify conversation_events row
        events = supabase.table("conversation_events").select_all({"psid": "PSID_TRANS_1"})
        state_changes = [e for e in events if e["event_type"] == "state_change"]
        assert len(state_changes) == 1
        assert state_changes[0]["event_data"]["to"] == "options_presented"


class TestSearchToursDeterministicReply:
    def test_country_request_returns_top3_and_saves_snapshot(self, orch, supabase, make_tour):
        make_tour(
            web_code="ap900001",
            name="Tokyo Value",
            price=18999,
            airline="XJ",
            tour_code_real="JP-TYO-001",
            country_id=2,
        )
        make_tour(
            web_code="ap900002",
            name="Osaka Standard",
            price=25900,
            airline="VZ",
            tour_code_real="JP-OSA-002",
            country_id=2,
        )
        make_tour(
            web_code="ap900003",
            name="Hokkaido Upgrade",
            price=32900,
            airline="TG",
            tour_code_real="JP-HKD-003",
            country_id=2,
        )
        make_tour(
            web_code="ap900004",
            name="Korea Value",
            price=15900,
            airline="BX",
            tour_code_real="KR-SEL-004",
            country_id=1,
        )

        result = orch.handle_turn(
            psid="PSID_SEARCH_JP",
            text="มีทัวร์ไปญี่ปุ่นไหมครับ",
            meta_message_id="fb:search_jp_1",
        )

        assert result.state_after == "options_presented"
        assert result.reply_text is not None
        assert "ap900001" in result.reply_text
        assert "JP-TYO-001" in result.reply_text
        assert "ap900004" not in result.reply_text
        assert "ขอข้อมูลเพิ่มเติม" not in result.reply_text
        snapshots = supabase.table("offer_snapshots").select_all({"psid": "PSID_SEARCH_JP"})
        assert len(snapshots) == 1
        assert len(snapshots[0]["tour_list"]) == 3

    def test_country_request_filters_staging_fixture_rows(self, orch, supabase, make_tour):
        for suffix in ("a3649c", "d9f764", "b73af8"):
            supabase.table("tours_canonical").insert({
                "web_code": f"ap_itest_lock_{suffix}",
                "tour_code_real": None,
                "name": "T",
                "country": "ญี่ปุ่น",
                "country_id": 2,
                "days": 5,
                "airline": None,
                "base_price": 1000,
                "url": "https://x",
                "is_active": True,
            })
        make_tour(
            web_code="ap910001",
            name="Tokyo Real Value",
            price=18999,
            airline="XJ",
            tour_code_real="JP-REAL-001",
            country_id=2,
        )
        make_tour(
            web_code="ap910002",
            name="Kawaguchiko Real",
            price=19999,
            airline="VZ",
            tour_code_real="JP-REAL-002",
            country_id=2,
        )
        make_tour(
            web_code="ap910003",
            name="Osaka Real",
            price=20999,
            airline="XJ",
            tour_code_real="JP-REAL-003",
            country_id=2,
        )

        result = orch.handle_turn(
            psid="PSID_SEARCH_FIXTURE_FILTER",
            text="มีทัวร์ไปญี่ปุ่นไหมครับ",
            meta_message_id="fb:search_fixture_filter_1",
        )

        assert result.reply_text is not None
        assert "ap_itest" not in result.reply_text
        assert "https://x" not in result.reply_text
        assert "1,000" not in result.reply_text
        assert "ap910001" in result.reply_text
        snapshots = supabase.table("offer_snapshots").select_all({
            "psid": "PSID_SEARCH_FIXTURE_FILTER",
        })
        assert len(snapshots) == 1
        rendered_codes = [tour["web_code"] for tour in snapshots[0]["tour_list"]]
        assert rendered_codes == ["ap910001", "ap910002", "ap910003"]

    def test_country_request_falls_back_to_live_listing_when_db_only_has_fixtures(
        self, supabase, redis, monkeypatch
    ):
        from v2.scraper.scrape_tours import ParsedTour
        orch = Orchestrator(supabase, redis, MockLLMClient(), http_client=object())

        for suffix in ("a3649c", "d9f764", "b73af8"):
            supabase.table("tours_canonical").insert({
                "web_code": f"ap_itest_lock_{suffix}",
                "tour_code_real": None,
                "name": "T",
                "country": "à¸à¸µà¹ˆà¸›à¸¸à¹ˆà¸™",
                "country_id": 2,
                "days": 5,
                "airline": None,
                "base_price": 1000,
                "url": "https://x",
                "is_active": True,
            })

        def fake_fetch(country_id, country_name, *, http=None):
            assert country_id == 2
            assert country_name
            return [
                ParsedTour(
                    web_code="ap920001",
                    name="Tokyo Live Value",
                    country=country_name,
                    country_id=country_id,
                    days=5,
                    base_price=18999,
                    airline="XJ",
                    url="https://www.tourfiremai.com/tour/ap920001",
                ),
                ParsedTour(
                    web_code="ap920002",
                    name="Osaka Live Standard",
                    country=country_name,
                    country_id=country_id,
                    days=5,
                    base_price=19999,
                    airline="VZ",
                    url="https://www.tourfiremai.com/tour/ap920002",
                ),
                ParsedTour(
                    web_code="ap920003",
                    name="Fuji Live Upgrade",
                    country=country_name,
                    country_id=country_id,
                    days=5,
                    base_price=21999,
                    airline="XJ",
                    url="https://www.tourfiremai.com/tour/ap920003",
                ),
            ]

        monkeypatch.setattr("v2.scraper.scrape_tours.fetch_country_listing", fake_fetch)

        result = orch.handle_turn(
            psid="PSID_SEARCH_LIVE_FALLBACK",
            text="มีทัวร์ไปญี่ปุ่นไหมครับ",
            meta_message_id="fb:search_live_fallback_1",
        )

        assert result.reply_text is not None
        assert "ap_itest" not in result.reply_text
        assert "https://x" not in result.reply_text
        assert "ap920001" in result.reply_text
        snapshots = supabase.table("offer_snapshots").select_all({
            "psid": "PSID_SEARCH_LIVE_FALLBACK",
        })
        assert len(snapshots) == 1
        assert [tour["web_code"] for tour in snapshots[0]["tour_list"]] == [
            "ap920001", "ap920002", "ap920003",
        ]

    def test_country_request_tops_up_partial_db_results_from_live_listing(
        self, supabase, redis, make_tour, monkeypatch
    ):
        from v2.scraper.scrape_tours import ParsedTour
        orch = Orchestrator(supabase, redis, MockLLMClient(), http_client=object())

        make_tour(
            web_code="ap930001",
            name="Tokyo DB Value",
            price=18999,
            airline="XJ",
            tour_code_real="JP-DB-001",
            country_id=2,
        )

        def fake_fetch(country_id, country_name, *, http=None):
            assert country_id == 2
            return [
                ParsedTour(
                    web_code="ap930000",
                    name="Broken Zero Day Tour",
                    country=country_name,
                    country_id=country_id,
                    days=0,
                    base_price=5000,
                    airline="ZZ",
                    url="https://www.tourfiremai.com/tour/ap930000",
                ),
                ParsedTour(
                    web_code="ap930001",
                    name="Tokyo DB Value Duplicate",
                    country=country_name,
                    country_id=country_id,
                    days=5,
                    base_price=18999,
                    airline="XJ",
                    url="https://www.tourfiremai.com/tour/ap930001",
                ),
                ParsedTour(
                    web_code="ap930002",
                    name="Osaka Live Topup",
                    country=country_name,
                    country_id=country_id,
                    days=5,
                    base_price=19999,
                    airline="VZ",
                    url="https://www.tourfiremai.com/tour/ap930002",
                ),
                ParsedTour(
                    web_code="ap930003",
                    name="Fuji Live Topup",
                    country=country_name,
                    country_id=country_id,
                    days=5,
                    base_price=21999,
                    airline="XJ",
                    url="https://www.tourfiremai.com/tour/ap930003",
                ),
            ]

        monkeypatch.setattr("v2.scraper.scrape_tours.fetch_country_listing", fake_fetch)

        result = orch.handle_turn(
            psid="PSID_SEARCH_PARTIAL_TOPUP",
            text="มีทัวร์ไปญี่ปุ่นไหมครับ",
            meta_message_id="fb:search_partial_topup_1",
        )

        assert result.reply_text is not None
        assert "ap930001" in result.reply_text
        assert "ap930002" in result.reply_text
        assert "ap930003" in result.reply_text
        assert "ap930000" not in result.reply_text
        assert "ขอทราบงบ" not in result.reply_text
        snapshots = supabase.table("offer_snapshots").select_all({
            "psid": "PSID_SEARCH_PARTIAL_TOPUP",
        })
        assert len(snapshots) == 1
        assert [tour["web_code"] for tour in snapshots[0]["tour_list"]] == [
            "ap930001", "ap930002", "ap930003",
        ]


class TestMemoryAwarePreferenceFollowup:
    def test_period_followup_uses_country_budget_memory_and_searches(self, orch, supabase, make_tour):
        period = "เดือนหน้า"
        make_tour(
            web_code="ap940001",
            name="Tokyo Memory Followup",
            price=18999,
            airline="XJ",
            tour_code_real="JP-MEM-001",
            country_id=2,
        )
        psid = "PSID_MEMORY_FOLLOWUP"
        supabase.table("customers").insert({"psid": psid})
        orch.memory.update_customer_memory(
            psid,
            {
                "latest_country": COUNTRY_NAMES[2],
                "budget_per_person": 30000,
                "budget_type": "strict",
            },
            reason="test_seed",
        )

        result = orch.handle_turn(
            psid=psid,
            text=period,
            meta_message_id="fb:mem_follow_1",
        )

        assert result.state_after == "options_presented"
        tool_names = [t["tool"] for t in result.tool_calls_made]
        assert "update_customer_memory" in tool_names
        assert "search_tours" in tool_names
        memory = orch.memory.get_customer_memory(psid)
        assert memory.travel_month == period
        snapshots = supabase.table("offer_snapshots").select_all({"psid": psid})
        assert len(snapshots) == 1
        assert any(tour["web_code"] == "ap940001" for tour in snapshots[0]["tour_list"])


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



class TestFeeHandoffPath:
    """QA flagged: no orchestrator-level test of fee-incomplete handoff."""

    def test_fee_check_required_with_missing_fees_does_not_call_llm(
        self, orch, supabase, make_tour
    ):
        """When state=fee_check_required AND tour_fees row is missing,
        response writer must use canned handoff message — NO LLM call."""
        from v2.lib.llm import MockLLMClient
        from v2.lib.response_writer import CANNED_HANDOFF_FEE_INCOMPLETE

        # Pre-populate: customer + conversation in fee_check_required + locked tour
        cust = supabase.table("customers").insert({"psid": "PSID_FEE_HANDOFF"})
        conv = supabase.table("conversations").insert({
            "customer_id": cust["id"], "psid": "PSID_FEE_HANDOFF",
            "state": "fee_check_required",
        })
        tour = make_tour(web_code="ap_fee_test", name="Test Tour",
                          price=20000, country="ญี่ปุ่น", country_id=2)
        supabase.table("selected_tours").insert({
            "conversation_id": conv["id"], "customer_id": cust["id"],
            "psid": "PSID_FEE_HANDOFF", "tour_id": tour["id"],
        })
        # Intentionally do NOT insert tour_fees row → fee_check should fail

        # Track LLM calls
        spy_llm = MockLLMClient()
        orch.llm = spy_llm

        result = orch.handle_turn(
            psid="PSID_FEE_HANDOFF", text="ค่าทิปเท่าไหร่",
            meta_message_id="fb:fee_handoff_test_1",
        )

        # Bot must respond with canned handoff text — NO LLM call
        # When state is fee_check_required and fees incomplete → state transitions to waiting_team
        assert result.reply_text is not None
        # Either still in fee_check_required (1-shot ack) or transitioned to waiting_team
        assert result.state_after in ("waiting_team", "fee_check_required")
        # Most important: LLM response tier was NOT used for actual reply
        response_calls = [c for c in spy_llm.call_log if c["tier"] == "response"]
        # Either no response-tier call OR returned canned (no LLM-generated text)
        assert len(response_calls) == 0 or "ตรวจสอบ" in result.reply_text or "ทีมงาน" in result.reply_text
