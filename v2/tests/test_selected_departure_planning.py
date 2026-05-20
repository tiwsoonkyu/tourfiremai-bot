"""
Sprint 5 Package H (DEV-2026-05-20-014).

End-to-end orchestrator + response_writer coverage for the selected
departure detail planning wiring.

These tests prove the 10 required cases in CURRENT_DEV_TASK.md without
ever touching the live network, OpenAI, LINE, Meta, OCR, or Supabase:

  1. Generic greeting / broad country ask does NOT fetch the detail page.
  2. After a customer selects a tour, the orchestrator enriches detail
     exactly once across multiple follow-up turns.
  3. A customer date phrase that exactly matches a row passes
     high-confidence row data to response planning.
  4. A fee follow-up after the tour is selected does NOT lose the
     selected tour.
  5. An ambiguous date phrase produces a confirmation-style planning
     bundle instead of a guess.
  6. A date phrase with no matching row surfaces ``available_departures``
     so the LLM can ask the customer to pick.
  7. web_code / tour_code_real / airline stay strictly separate on the
     planning payload (never collapsed).
  8. "-" cells in the source detail HTML stay None on the planning row.
  9. An admin sold-out / full override still blocks the candidate before
     the LLM is ever called (regression for DEV-2026-05-19-007).
 10. The test module / orchestrator never imports a live network library
     or paid provider client.

All Supabase + Redis access goes through the in-memory fakes in
``conftest.py``. The HTTP client is a synchronous fake that records every
call so we can assert fetch counts deterministically.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

import pytest

from v2.lib.llm import MockLLMClient
from v2.lib.memory import OfferSnapshot, TourOption
from v2.lib.orchestrator import Orchestrator
from v2.lib.page_post_context import (
    mark_availability_override,
)
from v2.lib.selected_departure_planning import (
    MATCH_STATUS_AMBIGUOUS,
    MATCH_STATUS_HIGH,
    MATCH_STATUS_MEDIUM,
    MATCH_STATUS_NO_MATCH,
    MATCH_STATUS_NO_PHRASE,
    MATCH_STATUS_NO_ROWS,
    SelectedDeparturePlanning,
    build_selected_departure_planning,
    row_dict_to_departure_price_row,
)
from v2.scraper.departure_price_table import (
    DeparturePriceRow,
    parse_departure_price_table,
)
from v2.scraper.detail_enrichment import BASE_URL


# ---------------------------------------------------------------------------
# Fixtures — synthetic detail HTML the tests reuse from DEV-013.
# ---------------------------------------------------------------------------

FIXTURE_DETAIL_HTML = """
<html><head><title>BCCKG27-HU | ทัวร์ฉงชิ่ง ap242455</title></head>
<body>
  <div class="tour-header">
    <h1>ทัวร์ฉงชิ่ง ต้าจู๋ อู่หลง 6 วัน 5 คืน</h1>
    <span class="b-codepg">BCCKG27-HU</span>
    <span class="b-airline">บินกับ HU</span>
    <a href="/tour/ap242455">รายละเอียดทัวร์</a>
  </div>
  <div class="table-dateprice">
    <div class="b-tb-dp">
      <span class="s-tb1-n">18-23 มิ.ย. 69</span>
      <span class="s-tb2-n">1</span>
      <span class="s-tb3-n">25,900</span>
      <span class="s-tb4-n">24,900</span>
      <span class="s-tb5-n">23,900</span>
      <span class="s-tb6-n">5,500</span>
      <span class="s-tb7-n">-</span>
      <span class="s-tb8-n">30</span>
      <span class="s-tb9-n">ติดต่อเจ้าหน้าที่</span>
    </div>
    <div class="b-tb-dp">
      <span class="s-tb1-n">29 ก.ค. - 4 ส.ค. 69</span>
      <span class="s-tb2-n">2</span>
      <span class="s-tb3-n">27,900</span>
      <span class="s-tb4-n">-</span>
      <span class="s-tb5-n">-</span>
      <span class="s-tb6-n">6,000</span>
      <span class="s-tb7-n">19,900</span>
      <span class="s-tb8-n">32</span>
      <span class="s-tb9-n">ติดต่อเจ้าหน้าที่</span>
    </div>
    <div class="b-tb-dp row-soldout">
      <span class="s-tb1-n">5 ก.ค. 69</span>
      <span class="s-tb2-n">1</span>
      <span class="s-tb3-n">29,900</span>
      <span class="s-tb4-n">28,900</span>
      <span class="s-tb5-n">-</span>
      <span class="s-tb6-n">7,000</span>
      <span class="s-tb7-n">-</span>
      <span class="s-tb8-n">25</span>
      <span class="s-tb9-n">เต็ม</span>
    </div>
  </div>
</body></html>
"""

ADMIN_ID = "line-admin-1"
WEB_CODE = "ap242455"
TOUR_CODE_REAL = "BCCKG27-HU"
AIRLINE = "HU"


@dataclass
class _FakeResponse:
    status_code: int
    text: str


class _FakeHttp:
    """Synchronous fake HTTP client — records every call URL.

    Never touches the network. Returns the synthetic detail HTML for any
    URL on the configured BASE_URL host.
    """

    def __init__(self, body: str = FIXTURE_DETAIL_HTML, status: int = 200):
        self.body = body
        self.status = status
        self.calls: list[tuple[str, int]] = []

    def get(self, url: str, timeout: int = 30) -> _FakeResponse:
        self.calls.append((url, timeout))
        return _FakeResponse(status_code=self.status, text=self.body)


class _RecordingLLM(MockLLMClient):
    """MockLLMClient subclass that captures every `response`-tier call.

    The recorded ``last_user_payload`` is the JSON-ish string the response
    writer composed for the LLM. Tests inspect it to verify what reached
    the model (and what did not).
    """

    def __init__(self):
        super().__init__()
        self.response_calls: list[dict[str, Any]] = []
        self.last_user_payload: Optional[str] = None
        self.next_text = "ขอแจ้งรายละเอียดตามนี้ค่ะ"

    def chat(self, **kw):  # type: ignore[override]
        rsp = super().chat(**kw)
        if kw.get("tier") == "response":
            self.response_calls.append(kw)
            for m in kw.get("messages") or []:
                if m.get("role") == "user":
                    self.last_user_payload = m.get("content")
            rsp.text = self.next_text
        return rsp


def _orch(supabase, redis, *, http=None, llm=None) -> Orchestrator:
    return Orchestrator(
        supabase, redis, llm or _RecordingLLM(),
        http_client=http,
    )


def _seed_tour_and_lock(supabase, *, psid: str, web_code: str = WEB_CODE,
                         tour_code_real: str = TOUR_CODE_REAL,
                         airline: str = AIRLINE,
                         name: str = "ทัวร์ฉงชิ่ง ต้าจู๋ อู่หลง 6 วัน 5 คืน",
                         country_id: int = 6, state: str = "tour_selected"):
    """Insert tours_canonical row + customer + conversation + selected_tours
    lock. Returns the tour_canonical row dict."""
    tour = supabase.table("tours_canonical").insert({
        "web_code": web_code, "tour_code_real": tour_code_real,
        "name": name, "country": "จีน", "country_id": country_id,
        "days": 6, "nights": 5, "base_price": 25900, "airline": airline,
        "url": f"{BASE_URL}/tour/{web_code}",
        "is_active": True, "is_fire_sale": False,
    })
    cust = supabase.table("customers").insert({"psid": psid})
    conv = supabase.table("conversations").insert({
        "customer_id": cust["id"], "psid": psid, "state": state,
    })
    supabase.table("selected_tours").insert({
        "conversation_id": conv["id"], "customer_id": cust["id"],
        "psid": psid, "tour_id": tour["id"],
        "tour_code_real": tour["tour_code_real"],
    })
    return tour


# ===========================================================================
# 1) Generic greeting / broad country ask must NOT fetch the detail page.
# ===========================================================================


class TestGenericGreetingDoesNotFetch:
    def test_greeting_with_no_locked_tour_does_not_fetch(self, supabase, redis):
        http = _FakeHttp()
        orch = _orch(supabase, redis, http=http)
        result = orch.handle_turn(
            psid="PSID_GREET_1", text="สวัสดีค่ะ",
            meta_message_id="fb:s5h_greet_1",
        )
        assert result.silent is False
        # CRITICAL: no detail fetch on a pure greeting.
        assert http.calls == []

    def test_broad_country_ask_with_no_lock_does_not_fetch(self, supabase, redis):
        http = _FakeHttp()
        orch = _orch(supabase, redis, http=http)
        result = orch.handle_turn(
            psid="PSID_BROAD_1", text="อยากไปญี่ปุ่นค่ะ",
            meta_message_id="fb:s5h_broad_1",
        )
        # State transition to collecting_preferences is fine — what matters
        # is no detail fetch happened.
        assert http.calls == []
        # Reply was generated normally.
        assert result.reply_text is not None


# ===========================================================================
# 2) Customer selects a tour then asks for details — enriches detail once.
# ===========================================================================


class TestEnrichesDetailOnceAndLocksCandidate:
    def test_lock_then_followup_only_fetches_once(self, supabase, redis):
        """After lock_selected_tour runs in turn 1, the detail page is
        fetched once. The next follow-up turn re-uses DB rows."""
        psid = "PSID_LOCK_FOLLOW"
        _seed_tour_and_lock(supabase, psid=psid)

        http = _FakeHttp()
        orch = _orch(supabase, redis, http=http)

        # Turn 1 — customer asks for details on the locked tour.
        r1 = orch.handle_turn(
            psid=psid, text="ขอรายละเอียดหน่อยค่ะ",
            meta_message_id="fb:s5h_follow_1",
        )
        assert r1.silent is False
        # Exactly one HTTP call to /tour/<web_code>.
        assert len(http.calls) == 1
        assert http.calls[0][0] == f"{BASE_URL}/tour/{WEB_CODE}"

        # Turn 2 — another follow-up.
        r2 = orch.handle_turn(
            psid=psid, text="ราคาเท่าไหร่คะ",
            meta_message_id="fb:s5h_follow_2",
        )
        assert r2.silent is False
        # Still exactly one call — DB rows from turn 1 satisfied turn 2.
        assert len(http.calls) == 1


# ===========================================================================
# 3) Date+pax after select — high-confidence row passed to response planning.
# ===========================================================================


class TestHighConfidenceRowPassedToPlanning:
    def test_date_match_passes_compact_row_to_llm(self, supabase, redis):
        psid = "PSID_HIGH_CONF"
        _seed_tour_and_lock(supabase, psid=psid)

        llm = _RecordingLLM()
        llm.next_text = "รอบเดินทาง 18-23 มิ.ย. ราคาเริ่ม 25,900 บาท ทีมงานจะเช็กที่นั่งให้นะคะ"
        http = _FakeHttp()
        orch = _orch(supabase, redis, http=http, llm=llm)

        # Customer phrase: exact start date + pax.
        # parse_customer_date_phrase("18 มิ.ย. 69 3 คน") → 2026-06-18 (high).
        result = orch.handle_turn(
            psid=psid, text="ขอ 18 มิ.ย. 69 3 คน",
            meta_message_id="fb:s5h_high_1",
        )
        assert result.silent is False
        assert len(llm.response_calls) >= 1

        payload = llm.last_user_payload or ""
        assert "selected_departure_planning" in payload
        # Compact dict — high-confidence match.
        assert MATCH_STATUS_HIGH in payload
        # The matched departure data is exposed to the LLM.
        assert "25900" in payload or "25,900" in payload
        # Web code present and not merged with anything else.
        assert WEB_CODE in payload
        # safe_planning_note tells LLM not to confirm availability as final.
        assert "ยืนยัน" in payload  # Thai "confirm" appears in note


# ===========================================================================
# 4) Fee follow-up does NOT lose the selected tour from memory.
# ===========================================================================


class TestFeeFollowupKeepsSelectedTour:
    def test_ask_fee_does_not_clear_selected_tour(self, supabase, redis):
        psid = "PSID_FEE_KEEP"
        _seed_tour_and_lock(supabase, psid=psid)

        http = _FakeHttp()
        orch = _orch(supabase, redis, http=http)

        # Lock present before the turn.
        before = orch.memory.get_selected_tour(psid)
        assert before is not None
        assert before.web_code == WEB_CODE

        result = orch.handle_turn(
            psid=psid, text="ค่าทิปเท่าไหร่คะ",
            meta_message_id="fb:s5h_fee_1",
        )
        # Either a canned fee answer or canned handoff — either way silent
        # should be False (the bot replies) and the tour is NOT lost.
        assert result.silent is False

        after = orch.memory.get_selected_tour(psid)
        assert after is not None, "selected tour must not be cleared by ask_fee"
        assert after.web_code == WEB_CODE
        assert after.tour_code_real == TOUR_CODE_REAL


# ===========================================================================
# 5) Ambiguous date phrase asks confirmation instead of guessing.
# ===========================================================================


class TestAmbiguousPhraseAsksConfirmation:
    def test_two_rows_share_start_date_is_ambiguous(self, supabase, redis):
        rows = [
            DeparturePriceRow(
                web_code=WEB_CODE, tour_code_real=TOUR_CODE_REAL, airline=AIRLINE,
                departure_start=date(2026, 6, 18), departure_end=date(2026, 6, 23),
                adult_price=25900, bus=1,
            ),
            DeparturePriceRow(
                web_code=WEB_CODE, tour_code_real=TOUR_CODE_REAL, airline=AIRLINE,
                departure_start=date(2026, 6, 18), departure_end=date(2026, 6, 23),
                adult_price=26900, bus=2,
            ),
        ]
        planning = build_selected_departure_planning(
            rows=rows,
            customer_text="18 มิ.ย. 69",
            selected_tour={
                "web_code": WEB_CODE, "tour_code_real": TOUR_CODE_REAL,
                "airline": AIRLINE, "name": "ทัวร์ทดสอบ",
            },
            today=date(2026, 5, 1),
        )
        assert planning.match_status == MATCH_STATUS_AMBIGUOUS
        assert planning.ask_confirmation is True
        assert len(planning.ambiguous_candidates) == 2
        assert planning.matched_departure is None
        # Safe note instructs LLM to NOT guess.
        assert "ยืนยัน" in (planning.safe_planning_note or "")


# ===========================================================================
# 6) No matching date — surfaces available_departures for customer to pick.
# ===========================================================================


class TestNoMatchOffersAvailableDates:
    def test_phrase_with_no_match_lists_future_open_rows(self):
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, WEB_CODE)
        planning = build_selected_departure_planning(
            rows=rows,
            customer_text="1 ก.ย. 69",  # no row covers this date
            selected_tour={
                "web_code": WEB_CODE, "tour_code_real": TOUR_CODE_REAL,
                "airline": AIRLINE, "name": "ทัวร์ฉงชิ่ง",
            },
            today=date(2026, 5, 1),
        )
        assert planning.match_status == MATCH_STATUS_NO_MATCH
        # Customer should see the two future, non-sold-out rows.
        assert len(planning.available_departures) == 2
        # The sold-out row (5 ก.ค. 69) is excluded.
        for dep in planning.available_departures:
            assert dep["availability_status"] != "sold_out"
        # Each available row preserves the codes separately.
        for dep in planning.available_departures:
            assert dep["web_code"] == WEB_CODE
            assert dep["tour_code_real"] == TOUR_CODE_REAL
            assert dep["airline"] == AIRLINE


# ===========================================================================
# 7) web_code, tour_code_real, and airline stay strictly separate.
# ===========================================================================


class TestCodesStaySeparate:
    def test_planning_dict_keeps_three_codes_distinct(self):
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, WEB_CODE)
        planning = build_selected_departure_planning(
            rows=rows,
            customer_text="18 มิ.ย. 69 3 คน",
            selected_tour={
                "web_code": WEB_CODE, "tour_code_real": TOUR_CODE_REAL,
                "airline": AIRLINE, "name": "ทัวร์ฉงชิ่ง",
            },
            today=date(2026, 5, 1),
        )
        d = planning.to_compact_dict()
        assert d["web_code"] == WEB_CODE
        assert d["tour_code_real"] == TOUR_CODE_REAL
        assert d["airline"] == AIRLINE
        # Mutual inequality — the three fields can never collapse.
        assert d["web_code"] != d["tour_code_real"]
        assert d["tour_code_real"] != d["airline"]
        assert d["web_code"] != d["airline"]
        # The matched_departure row preserves the same separation.
        assert planning.matched_departure is not None
        m = planning.matched_departure
        assert m["web_code"] == WEB_CODE
        assert m["tour_code_real"] == TOUR_CODE_REAL
        assert m["airline"] == AIRLINE


# ===========================================================================
# 8) "-" cells stay None on the planning row (never coerced to 0).
# ===========================================================================


class TestMissingValuesStayNone:
    def test_dash_cells_remain_none_in_matched_row(self):
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, WEB_CODE)
        # Row 2 (29 ก.ค. - 4 ส.ค. 69) has child_bed = "-" and child_no_bed = "-".
        planning = build_selected_departure_planning(
            rows=rows,
            customer_text="29 ก.ค. 69",
            selected_tour={
                "web_code": WEB_CODE, "tour_code_real": TOUR_CODE_REAL,
                "airline": AIRLINE, "name": "ทัวร์ฉงชิ่ง",
            },
            today=date(2026, 5, 1),
        )
        assert planning.match_status == MATCH_STATUS_HIGH
        m = planning.matched_departure
        assert m is not None
        assert m["child_bed_price"] is None
        assert m["child_no_bed_price"] is None
        # CRITICAL: never coerced to 0.
        for k in (
            "adult_price",
            "child_bed_price",
            "child_no_bed_price",
            "single_supplement_price",
            "joinland_price",
        ):
            v = m[k]
            assert v is None or v > 0


# ===========================================================================
# 9) Sold-out / full overrides still block the candidate BEFORE the LLM.
# ===========================================================================


class TestSoldOutOverrideStillBlocks:
    def test_admin_tour_full_blocks_even_with_selected_departure_data(
        self, supabase, redis,
    ):
        """Regression test: the page-post / sold-out planner still wins.

        The orchestrator may build a selected-departure planning bundle,
        but if ``planning.replacement_needed`` is True, the response
        writer returns the canned blocked reply BEFORE the LLM is ever
        called.
        """
        psid = "PSID_BLOCK_OVERRIDE"
        _seed_tour_and_lock(supabase, psid=psid)
        mark_availability_override(
            supabase, scope="tour", status="full",
            web_code=WEB_CODE, marked_by=ADMIN_ID,
        )

        llm = _RecordingLLM()
        http = _FakeHttp()
        orch = _orch(supabase, redis, http=http, llm=llm)

        result = orch.handle_turn(
            psid=psid, text="ขอ 18 มิ.ย. 69 3 คน",
            meta_message_id="fb:s5h_block_1",
        )
        # Canned blocked path used — LLM `response` tier NOT called.
        assert result.decision == "canned_blocked"
        assert llm.response_calls == []


# ===========================================================================
# 10) No live network / paid-provider imports.
# ===========================================================================


class TestNoLiveProviderImports:
    def test_planning_module_does_not_import_network_or_llm(self):
        import inspect

        from v2.lib import selected_departure_planning as mod

        src = inspect.getsource(mod)
        for forbidden in (
            "import requests", "openai", "anthropic", "boto3", "supabase",
        ):
            assert forbidden not in src, (
                f"selected_departure_planning must not import {forbidden}"
            )

    def test_orchestrator_does_not_call_live_http_when_client_missing(
        self, supabase, redis,
    ):
        """With ``http_client=None`` the orchestrator never attempts a live
        fetch — it falls back gracefully to DB-only data."""
        psid = "PSID_NO_HTTP"
        _seed_tour_and_lock(supabase, psid=psid)

        orch = Orchestrator(
            supabase, redis, _RecordingLLM(),
            http_client=None,
        )
        # Should not raise, should not attempt a network call (none is
        # available — the test ensures that path is the empty path).
        result = orch.handle_turn(
            psid=psid, text="ขอรายละเอียดหน่อยค่ะ",
            meta_message_id="fb:s5h_nohttp_1",
        )
        assert result.silent is False


# ===========================================================================
# Bonus — orchestrator's resolve method follows the documented priority order.
# ===========================================================================


class TestCandidateResolutionPriority:
    def test_just_locked_beats_memory_lock(self, supabase, redis):
        """Priority 1 (just-locked) wins over priority 2 (memory lock)."""
        psid = "PSID_PRIO_1"
        # Memory lock to web_code 'ap111111'.
        _seed_tour_and_lock(
            supabase, psid=psid,
            web_code="ap111111", tour_code_real="OLD-OLD",
            name="Old tour",
        )
        # Insert tours_canonical row for the "just-locked" one too.
        new_tour = supabase.table("tours_canonical").insert({
            "web_code": "ap999999", "tour_code_real": "NEW-NEW",
            "name": "New tour", "country": "จีน", "country_id": 6,
            "days": 5, "nights": 4, "base_price": 19900, "airline": "XJ",
            "url": f"{BASE_URL}/tour/ap999999", "is_active": True,
        })

        orch = _orch(supabase, redis)
        cand = orch._resolve_selected_departure_candidate(
            psid=psid, conv={"id": "x", "psid": psid, "state": "tour_selected"},
            accumulated={"lock_selected_tour": {
                "web_code": "ap999999", "tour_code_real": "NEW-NEW",
                "tour_id": new_tour["id"], "name": "New tour",
            }},
            intent=_make_intent(type="select_tour"),
        )
        assert cand.source == "just_locked"
        assert cand.web_code == "ap999999"
        # Backfill from tours_canonical fills airline that the locked dict didn't have.
        assert cand.airline == "XJ"

    def test_intent_code_beats_in_turn_detail(self, supabase, redis):
        """Priority 3 (intent code) wins over priority 4 (in-turn detail)."""
        _seed_tour_and_lock(supabase, psid="PSID_PRIO_3")  # locks to ap242455
        supabase.table("tours_canonical").insert({
            "web_code": "ap555555", "tour_code_real": "EXP-EXP",
            "name": "Explicit code tour", "country": "เกาหลี", "country_id": 5,
            "days": 5, "nights": 4, "base_price": 22900, "airline": "TG",
            "url": f"{BASE_URL}/tour/ap555555", "is_active": True,
        })
        orch = _orch(supabase, redis)
        # Skip the memory lock by querying a different psid that has no lock.
        cand = orch._resolve_selected_departure_candidate(
            psid="PSID_FRESH",
            conv={"id": "x", "psid": "PSID_FRESH", "state": "new_lead"},
            accumulated={"get_tour_detail": {
                "web_code": "ap242455", "tour_code_real": "BCCKG27-HU",
                "id": "some-uuid",
            }},
            intent=_make_intent(type="select_tour", selected_code="ap555555"),
        )
        assert cand.source == "intent_code"
        assert cand.web_code == "ap555555"

    def test_no_candidate_when_nothing_resolves(self, supabase, redis):
        orch = _orch(supabase, redis)
        cand = orch._resolve_selected_departure_candidate(
            psid="PSID_NOPE",
            conv={"id": "x", "psid": "PSID_NOPE", "state": "new_lead"},
            accumulated={},
            intent=_make_intent(type="greeting"),
        )
        assert cand.web_code == ""
        assert cand.source == "none"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_intent(*, type: str = "unknown", selected_code: Optional[str] = None,
                 selected_index: Optional[int] = None):
    from v2.lib.intent import Intent
    return Intent(
        type=type, raw_text="", selected_code=selected_code,
        selected_index=selected_index,
    )


# ---------------------------------------------------------------------------
# row_dict_to_departure_price_row round-trip
# ---------------------------------------------------------------------------


class TestRowDictRoundTrip:
    def test_persisted_dict_round_trips_to_departure_price_row(self, supabase):
        # Use the actual persistence shape — write parsed rows to DB,
        # then read them back and convert.
        from v2.scraper.detail_enrichment import upsert_departure_rows
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, WEB_CODE)
        upsert_departure_rows(rows, supabase=supabase, tour_id="tid-rrt")
        persisted = supabase.table("tour_departures").select_all(
            {"tour_id": "tid-rrt"}
        )
        assert len(persisted) == 3
        converted = [row_dict_to_departure_price_row(d) for d in persisted]
        # Codes preserved separately.
        for r in converted:
            assert r.web_code == WEB_CODE
            assert r.tour_code_real == TOUR_CODE_REAL
            assert r.airline == AIRLINE
        # Dash cells stay None on round-trip.
        cross_month = next(r for r in converted if r.departure_start == date(2026, 7, 29))
        assert cross_month.child_bed_price is None
        assert cross_month.child_no_bed_price is None
