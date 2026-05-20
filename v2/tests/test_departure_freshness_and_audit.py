"""
Sprint 5 Package I (DEV-2026-05-20-015).

End-to-end coverage for the four sub-deliverables in this package:

  1. Canonical listing URL fix — ``tours_canonical.url`` is
     ``/tour/<web_code>`` (never ``/intertourdetail/...``).
  2. Departure-row freshness — newly upserted rows carry a freshness
     timestamp; the orchestrator/scheduler honor a TTL.
  3. Scheduled refresher — ``v2.tools.refresh_departure_rows``
     dry-run does not write to the DB; non-dry runs are bounded.
  4. Uniqueness readiness — ``v2.tools.departure_duplicate_audit``
     finds duplicates by the intended logical key and reports
     ``safe_for_unique_index`` correctly.

All Supabase + Redis access goes through the in-memory fakes in
``v2/tests/conftest.py``. The HTTP client is a synchronous fake that
records every call so we can assert fetch counts deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from v2.lib.llm import MockLLMClient
from v2.lib.orchestrator import Orchestrator
from v2.scraper.detail_enrichment import (
    BASE_URL,
    enrich_tour_detail,
    upsert_departure_rows,
)
from v2.scraper.departure_price_table import parse_departure_price_table
from v2.scraper.scrape_tours import (
    BASE_URL as SCRAPER_BASE_URL,
    parse_listing_html,
    upsert_tours_to_canonical,
)
from v2.tools.departure_duplicate_audit import (
    DUPLICATE_AUDIT_SQL,
    find_duplicates,
)
from v2.tools.refresh_departure_rows import (
    collect_selected_tour_web_codes,
    collect_stale_web_codes,
    refresh_departure_rows,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WEB_CODE = "ap242455"
TOUR_CODE_REAL = "BCCKG27-HU"
AIRLINE = "HU"

FIXTURE_LISTING_HTML = """
<html><body>
<div class="tour-card">
  <a href="/intertourdetail/ap242455">
    <h3>ทัวร์ฉงชิ่ง 6 วัน 5 คืน</h3>
  </a>
  <span>ราคา 25,900 บาท</span>
  <span>6 วัน 5 คืน</span>
  <span>บินกับ HU</span>
  <span>18-23 มิ.ย. 69</span>
</div>
</body></html>
"""

FIXTURE_DETAIL_HTML = """
<html><body>
  <span class="b-codepg">BCCKG27-HU</span>
  <span class="b-airline">บินกับ HU</span>
  <a href="/tour/ap242455">link</a>
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
  </div>
</body></html>
"""


@dataclass
class _Resp:
    status_code: int
    text: str


class _FakeHttp:
    def __init__(self, body: str = FIXTURE_DETAIL_HTML, status: int = 200,
                 raise_exc: Optional[Exception] = None):
        self.body = body
        self.status = status
        self.raise_exc = raise_exc
        self.calls: list[str] = []

    def get(self, url: str, timeout: int = 30) -> _Resp:
        self.calls.append(url)
        if self.raise_exc is not None:
            raise self.raise_exc
        return _Resp(status_code=self.status, text=self.body)


def _seed_tour_and_lock(supabase, *, psid: str = "PSID_TEST",
                         web_code: str = WEB_CODE):
    tour = supabase.table("tours_canonical").insert({
        "web_code": web_code, "tour_code_real": TOUR_CODE_REAL,
        "name": "ทัวร์ฉงชิ่ง 6 วัน 5 คืน", "country": "จีน", "country_id": 6,
        "days": 6, "nights": 5, "base_price": 25900, "airline": AIRLINE,
        "url": f"{BASE_URL}/tour/{web_code}",
        "is_active": True, "is_fire_sale": False,
    })
    cust = supabase.table("customers").insert({"psid": psid})
    conv = supabase.table("conversations").insert({
        "customer_id": cust["id"], "psid": psid, "state": "tour_selected",
    })
    supabase.table("selected_tours").insert({
        "conversation_id": conv["id"], "customer_id": cust["id"],
        "psid": psid, "tour_id": tour["id"],
        "tour_code_real": tour["tour_code_real"],
    })
    return tour


# ===========================================================================
# 1. Canonical listing URL fix — /tour/<web_code>
# ===========================================================================


class TestCanonicalListingUrl:
    def test_parsed_listing_emits_tour_path_not_intertourdetail(self):
        tours = parse_listing_html(
            FIXTURE_LISTING_HTML, country="ญี่ปุ่น", country_id=2,
        )
        assert tours, "fixture should produce at least one tour"
        for t in tours:
            assert t.url.startswith(f"{SCRAPER_BASE_URL}/tour/")
            assert "/intertourdetail/" not in t.url

    def test_upserted_canonical_url_uses_tour_path(self, supabase):
        tours = parse_listing_html(
            FIXTURE_LISTING_HTML, country="ญี่ปุ่น", country_id=2,
        )
        upsert_tours_to_canonical(tours, supabase)
        row = supabase.table("tours_canonical").select_one(
            {"web_code": WEB_CODE}
        )
        assert row is not None
        assert row["url"] == f"{SCRAPER_BASE_URL}/tour/{WEB_CODE}"
        assert "/intertourdetail/" not in row["url"]

    def test_no_v2_canonical_url_uses_intertourdetail_in_scraper_source(self):
        """Regression: scrape_tours must not emit the legacy detail path
        anywhere it builds a canonical URL."""
        import inspect

        from v2.scraper import scrape_tours as st

        src = inspect.getsource(st)
        # The legacy path may still appear in:
        # - comments / regex docstrings (TOUR_LINK_RE matches both),
        # but it MUST NOT appear inside an f-string used to build the URL.
        assert 'f"{BASE_URL}/intertourdetail/' not in src
        # The canonical URL builder must use /tour/.
        assert 'f"{BASE_URL}/tour/' in src

    def test_conftest_fixture_url_uses_tour_path(self, supabase, make_tour):
        # The make_tour helper in conftest must produce the new URL too.
        row = make_tour(web_code="apX01010", name="t", price=10000)
        assert row["url"].startswith("https://www.tourfiremai.com/tour/")
        assert "/intertourdetail/" not in row["url"]


# ===========================================================================
# 2. Departure-row freshness — refreshed_at metadata
# ===========================================================================


class TestRefreshedAtMetadata:
    def test_upsert_stamps_refreshed_at(self, supabase):
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, WEB_CODE)
        pinned = datetime(2026, 5, 20, 1, 0, 0, tzinfo=timezone.utc)
        result = upsert_departure_rows(
            rows, supabase=supabase, tour_id="tid-fresh",
            refreshed_at=pinned,
        )
        assert result.inserted == 2
        stored = supabase.table("tour_departures").select_all(
            {"tour_id": "tid-fresh"}
        )
        assert stored
        for r in stored:
            assert r["refreshed_at"] == pinned.isoformat()

    def test_enrich_pins_refreshed_at_to_fetched_at(self, supabase):
        http = _FakeHttp()
        pinned = datetime(2026, 5, 20, 2, 0, 0, tzinfo=timezone.utc)
        result = enrich_tour_detail(
            WEB_CODE, http=http, supabase=supabase, tour_id="tid-enrich",
            now=pinned,
        )
        assert result.persisted is True
        stored = supabase.table("tour_departures").select_all(
            {"tour_id": "tid-enrich"}
        )
        for r in stored:
            assert r["refreshed_at"] == pinned.isoformat()


# ===========================================================================
# 2b. Orchestrator freshness gate — fresh rows reused, stale rows refreshed
# ===========================================================================


class TestOrchestratorFreshnessGate:
    def test_fresh_db_rows_do_not_trigger_http_fetch(self, supabase, redis):
        psid = "PSID_FRESH"
        tour = _seed_tour_and_lock(supabase, psid=psid)
        # Seed a fresh row.
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, WEB_CODE)
        fresh_now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
        upsert_departure_rows(
            rows, supabase=supabase, tour_id=tour["id"], refreshed_at=fresh_now,
        )

        http = _FakeHttp()
        # `now` is right at the freshness write — TTL=1h, well under it.
        orch = Orchestrator(
            supabase, redis, MockLLMClient(),
            http_client=http,
            detail_freshness_ttl_s=3600,
            now=lambda: fresh_now + timedelta(minutes=30),
        )
        result = orch.handle_turn(
            psid=psid, text="ขอ 18 มิ.ย. 69 3 คน",
            meta_message_id="fb:s5i_fresh_1",
        )
        assert result.silent is False
        # No HTTP fetch because DB rows are fresh.
        assert http.calls == []

    def test_stale_db_rows_trigger_one_bounded_refresh(self, supabase, redis):
        psid = "PSID_STALE"
        tour = _seed_tour_and_lock(supabase, psid=psid)
        # Seed a STALE row.
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, WEB_CODE)
        stale_ts = datetime(2026, 5, 19, 0, 0, 0, tzinfo=timezone.utc)
        upsert_departure_rows(
            rows, supabase=supabase, tour_id=tour["id"], refreshed_at=stale_ts,
        )

        http = _FakeHttp()
        now_d = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
        orch = Orchestrator(
            supabase, redis, MockLLMClient(),
            http_client=http,
            detail_freshness_ttl_s=3600,  # 1h — row is much older
            now=lambda: now_d,
        )
        # Turn 1 — stale rows trip refresh; exactly one HTTP call.
        orch.handle_turn(
            psid=psid, text="ขอ 18 มิ.ย. 69 3 คน",
            meta_message_id="fb:s5i_stale_1",
        )
        assert len(http.calls) == 1
        assert http.calls[0] == f"{BASE_URL}/tour/{WEB_CODE}"

        # Turn 2 — DB rows are now fresh (enrich stamped them) AND the
        # in-memory guard is hot. Either way: no second HTTP call.
        orch.handle_turn(
            psid=psid, text="ค่าทิปเท่าไหร่",
            meta_message_id="fb:s5i_stale_2",
        )
        assert len(http.calls) == 1

    def test_refresh_failure_falls_back_to_stale_no_loop(self, supabase, redis):
        """Refresh failure must NOT block the bot and must NOT loop."""
        psid = "PSID_REFRESH_FAIL"
        tour = _seed_tour_and_lock(supabase, psid=psid)
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, WEB_CODE)
        stale_ts = datetime(2026, 5, 19, 0, 0, 0, tzinfo=timezone.utc)
        upsert_departure_rows(
            rows, supabase=supabase, tour_id=tour["id"], refreshed_at=stale_ts,
        )

        http = _FakeHttp(raise_exc=ConnectionError("boom"))
        now_d = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
        orch = Orchestrator(
            supabase, redis, MockLLMClient(),
            http_client=http,
            detail_freshness_ttl_s=3600,
            now=lambda: now_d,
        )

        r1 = orch.handle_turn(
            psid=psid, text="ขอ 18 มิ.ย. 69 3 คน",
            meta_message_id="fb:s5i_fail_1",
        )
        # Bot still replied — refresh failure must not blank the turn.
        assert r1.silent is False
        # One refresh attempt happened (fetch was called once).
        assert len(http.calls) == 1

        # Turn 2 — guard is hot so we don't retry the HTTP immediately.
        r2 = orch.handle_turn(
            psid=psid, text="ค่าทิปเท่าไหร่",
            meta_message_id="fb:s5i_fail_2",
        )
        assert r2.silent is False
        # No second HTTP attempt: still 1.
        assert len(http.calls) == 1


# ===========================================================================
# 3. Scheduled refresher — dry-run + bounded + stale-only audit
# ===========================================================================


class TestRefresherDryRun:
    def test_dry_run_records_intent_but_writes_nothing(self, supabase):
        # Seed stale rows for two web_codes.
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, WEB_CODE)
        stale_ts = datetime(2026, 5, 19, 0, 0, 0, tzinfo=timezone.utc)
        upsert_departure_rows(
            rows, supabase=supabase, tour_id="t-a", refreshed_at=stale_ts,
        )
        rows_count_before = len(
            supabase.table("tour_departures").select_all({}) or []
        )
        http = _FakeHttp()

        summary = refresh_departure_rows(
            [WEB_CODE], supabase=supabase, http_client=http, dry_run=True,
            now=datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc),
        )
        assert summary.requested == 1
        assert summary.skipped_dry_run == 1
        assert summary.refreshed == 0
        # No HTTP call.
        assert http.calls == []
        # No new rows inserted.
        rows_count_after = len(
            supabase.table("tour_departures").select_all({}) or []
        )
        assert rows_count_before == rows_count_after


class TestRefresherSelectedTours:
    def test_collect_selected_tour_web_codes_dedups(self, supabase):
        _seed_tour_and_lock(supabase, psid="PSID_A", web_code="ap111111")
        _seed_tour_and_lock(supabase, psid="PSID_B", web_code="ap222222")
        result = collect_selected_tour_web_codes(supabase)
        assert set(result) == {"ap111111", "ap222222"}


class TestRefresherStaleOnly:
    def test_collect_stale_web_codes_excludes_fresh_rows(self, supabase):
        # One fresh row + one stale row, different web_codes.
        now_d = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
        fresh_rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, "apFRESH1")
        upsert_departure_rows(
            fresh_rows, supabase=supabase, tour_id="t-fresh", refreshed_at=now_d,
        )
        stale_rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, "apSTALE1")
        upsert_departure_rows(
            stale_rows, supabase=supabase, tour_id="t-stale",
            refreshed_at=now_d - timedelta(days=1),
        )
        result = collect_stale_web_codes(
            supabase, ttl_s=3600, now=now_d,
        )
        assert "apstale1" in [c.lower() for c in result]
        assert "apfresh1" not in [c.lower() for c in result]

    def test_refresh_failure_recorded_per_web_code_no_loop(self, supabase):
        # Seed stale rows so the refresher actually attempts an HTTP call.
        stale_ts = datetime(2026, 5, 19, 0, 0, 0, tzinfo=timezone.utc)
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, WEB_CODE)
        upsert_departure_rows(
            rows, supabase=supabase, tour_id="t-fail", refreshed_at=stale_ts,
        )
        http = _FakeHttp(raise_exc=ConnectionError("boom"))
        summary = refresh_departure_rows(
            [WEB_CODE], supabase=supabase, http_client=http, dry_run=False,
            now=datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc),
            ttl_s=3600,
        )
        assert summary.requested == 1
        assert summary.failed == 1
        assert summary.refreshed == 0
        # Existing rows are still intact (refresh failure is fail-closed).
        stored = supabase.table("tour_departures").select_all({"tour_id": "t-fail"})
        assert len(stored) == 2

    def test_no_http_client_records_action(self, supabase):
        # Seed STALE rows so the freshness gate trips and we actually
        # reach the no_http_client branch.
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, WEB_CODE)
        stale_ts = datetime(2026, 5, 19, 0, 0, 0, tzinfo=timezone.utc)
        upsert_departure_rows(
            rows, supabase=supabase, tour_id="t-noh", refreshed_at=stale_ts,
        )
        now_d = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
        summary = refresh_departure_rows(
            [WEB_CODE], supabase=supabase, http_client=None, dry_run=False,
            now=now_d, ttl_s=3600,
        )
        assert summary.no_http_client == 1
        assert summary.refreshed == 0


# ===========================================================================
# 4. Duplicate audit + gated UNIQUE proposal
# ===========================================================================


class TestDuplicateAudit:
    def test_finds_duplicate_with_intended_logical_key(self, supabase):
        # Two rows that collide on (tour_id, start, end, bus).
        common = {
            "tour_id": "tdup",
            "web_code": WEB_CODE,
            "departure_start": "2026-06-18",
            "departure_end": "2026-06-23",
            "bus": 1,
        }
        supabase.table("tour_departures").insert(dict(common))
        supabase.table("tour_departures").insert(dict(common))
        # Plus a non-duplicate row.
        supabase.table("tour_departures").insert({
            "tour_id": "tdup", "web_code": WEB_CODE,
            "departure_start": "2026-07-05", "departure_end": "2026-07-05",
            "bus": 1,
        })
        result = find_duplicates(supabase)
        assert result.total_rows == 3
        assert result.rows_with_start == 3
        assert len(result.duplicate_groups) == 1
        g = result.duplicate_groups[0]
        assert g.tour_id == "tdup"
        assert g.departure_start == "2026-06-18"
        assert g.bus_key == 1
        assert g.count == 2
        assert result.safe_for_unique_index is False

    def test_audit_safe_when_no_duplicates(self, supabase):
        supabase.table("tour_departures").insert({
            "tour_id": "tok", "web_code": WEB_CODE,
            "departure_start": "2026-06-18", "departure_end": "2026-06-23",
            "bus": 1,
        })
        result = find_duplicates(supabase)
        assert result.duplicate_groups == []
        assert result.safe_for_unique_index is True

    def test_null_start_rows_excluded_from_audit(self, supabase):
        # NULL departure_start rows must not pollute the duplicate count
        # since the proposed partial UNIQUE index also excludes them.
        supabase.table("tour_departures").insert({
            "tour_id": "tnull", "web_code": WEB_CODE,
            "departure_start": None,
        })
        supabase.table("tour_departures").insert({
            "tour_id": "tnull", "web_code": WEB_CODE,
            "departure_start": None,
        })
        result = find_duplicates(supabase)
        assert result.total_rows == 2
        assert result.rows_with_start == 0
        assert result.duplicate_groups == []

    def test_sql_audit_block_is_read_only_and_targets_correct_columns(self):
        # The string is shipped as a constant — keep it deterministic so
        # operators always copy a known-safe SELECT.
        assert "SELECT" in DUPLICATE_AUDIT_SQL
        assert "FROM tour_departures" in DUPLICATE_AUDIT_SQL
        assert "GROUP BY tour_id, departure_start, departure_end, COALESCE(bus, 0)" in DUPLICATE_AUDIT_SQL
        # No DDL or DML allowed in the audit string.
        for forbidden in (
            "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE",
        ):
            assert forbidden not in DUPLICATE_AUDIT_SQL.upper().split("--", 1)[0]


# ===========================================================================
# 4b. Migration files exist + uniqueness proposal is NOT in *.sql glob
# ===========================================================================


class TestMigrationFiles:
    def test_022_freshness_migration_exists(self):
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..", "supabase", "migrations",
            "20260520_022_departure_refreshed_at.sql",
        )
        assert os.path.exists(path)
        with open(path) as f:
            sql = f.read()
        assert "ADD COLUMN IF NOT EXISTS refreshed_at" in sql
        assert "TIMESTAMPTZ" in sql
        # Must NOT add a UNIQUE constraint/index in this migration.
        # The word can appear in comments (we explicitly point to the
        # deferred uniqueness proposal); only DDL counts.
        ddl_only = "\n".join(
            line for line in sql.splitlines() if not line.lstrip().startswith("--")
        )
        assert "UNIQUE" not in ddl_only.upper()

    def test_uniqueness_proposal_is_not_a_sql_file(self):
        import os
        migrations = os.path.join(
            os.path.dirname(__file__), "..", "supabase", "migrations",
        )
        files = os.listdir(migrations)
        # The proposal must NOT be picked up by any *.sql glob.
        sql_files = [f for f in files if f.endswith(".sql")]
        proposal_files = [
            f for f in files if "departure_unique" in f and "proposal" in f
        ]
        assert any(".sql.proposal" in f for f in proposal_files), (
            "proposal must exist as .sql.proposal"
        )
        for f in proposal_files:
            assert f not in sql_files, (
                "uniqueness proposal must not appear in the *.sql glob"
            )
