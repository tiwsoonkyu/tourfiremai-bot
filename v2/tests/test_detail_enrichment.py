"""
Sprint 5 Package G (DEV-2026-05-20-013).

Tests for v2.scraper.detail_enrichment — wiring the DEV-012 detail-page
parser into the V2 scraper / detail-enrichment flow and persisting parsed
rows into ``tour_departures`` idempotently.

Hard rules under test:
    - Detail reads MUST use /tour/<web_code>, NEVER /intertourdetail/.
    - "-" cells stay None / NULL — never coerced to 0.
    - web_code, tour_code_real, and airline stay separate.
    - Generic contact-button text is never reclassified as sold-out.
    - The persistence helper is idempotent (re-running yields no extra rows).
    - No live network in unit tests (HTTP client is a fake).
    - No LLM, no Supabase real client, no secrets, no V1 changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

import pytest

from v2.scraper.detail_enrichment import (
    BASE_URL,
    DETAIL_PATH,
    DetailEnrichmentResult,
    DetailPersistenceResult,
    build_detail_url,
    enrich_tour_detail,
    fetch_detail_html,
    upsert_departure_rows,
)
from v2.scraper.departure_price_table import (
    DeparturePriceRow,
    parse_departure_price_table,
)


# ---------------------------------------------------------------------------
# Synthetic fixture — same shape as the DEV-012 parser tests.
# Includes a sold-out row, a row with "-" cells, and the contact-button row
# so we can re-assert the cross-cutting rules from this wiring layer.
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


# ---------------------------------------------------------------------------
# Fake HTTP client — explicit, never reaches the network. Tracks every
# call so tests can assert the URL shape.
# ---------------------------------------------------------------------------


@dataclass
class FakeResponse:
    status_code: int
    text: str


class FakeHttp:
    def __init__(self, *, body: str = FIXTURE_DETAIL_HTML, status: int = 200):
        self.body = body
        self.status = status
        self.calls: list[tuple[str, int]] = []

    def get(self, url: str, timeout: int = 30) -> FakeResponse:
        self.calls.append((url, timeout))
        return FakeResponse(status_code=self.status, text=self.body)


class RaisingHttp:
    """HTTP fake that raises like ``requests`` would on connection failure."""

    def __init__(self):
        self.calls: list[str] = []

    def get(self, url: str, timeout: int = 30):
        self.calls.append(url)
        raise ConnectionError("simulated network failure")


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


class TestBuildDetailUrl:
    def test_builds_tour_path_not_intertourdetail(self):
        url = build_detail_url("ap242455")
        assert url == f"{BASE_URL}/tour/ap242455"
        assert "/intertourdetail/" not in url

    def test_lowercases_web_code(self):
        assert build_detail_url("AP242455") == f"{BASE_URL}/tour/ap242455"

    def test_uses_canonical_template(self):
        # Regression guard: any future drift in DETAIL_PATH must keep /tour/.
        assert DETAIL_PATH.startswith("/tour/")

    def test_rejects_empty_web_code(self):
        with pytest.raises(ValueError):
            build_detail_url("")


# ---------------------------------------------------------------------------
# fetch_detail_html
# ---------------------------------------------------------------------------


class TestFetchDetailHtml:
    def test_fetches_tour_path(self):
        http = FakeHttp()
        body = fetch_detail_html("ap242455", http=http)
        assert body == FIXTURE_DETAIL_HTML
        assert http.calls == [(f"{BASE_URL}/tour/ap242455", 30)]
        # Hard rule guard: never hit the broken legacy path.
        assert "/intertourdetail/" not in http.calls[0][0]

    def test_non_200_returns_none(self):
        http = FakeHttp(status=500)
        assert fetch_detail_html("ap242455", http=http) is None

    def test_empty_body_returns_none(self):
        http = FakeHttp(body="")
        assert fetch_detail_html("ap242455", http=http) is None

    def test_connection_error_returns_none(self):
        http = RaisingHttp()
        assert fetch_detail_html("ap242455", http=http) is None
        # Single attempt — no retry storms from this layer.
        assert len(http.calls) == 1


# ---------------------------------------------------------------------------
# upsert_departure_rows — persistence, idempotency, mirroring, no zero
# ---------------------------------------------------------------------------


class TestUpsertDepartureRows:
    def _parsed(self) -> list[DeparturePriceRow]:
        return parse_departure_price_table(FIXTURE_DETAIL_HTML, "ap242455")

    def test_first_run_inserts_each_row(self, supabase):
        rows = self._parsed()
        result = upsert_departure_rows(rows, supabase=supabase, tour_id="tour-uuid-1")
        assert result.upserted == 3
        assert result.inserted == 3
        assert result.updated == 0
        assert result.skipped_no_date == 0
        assert not result.errors

        stored = supabase.table("tour_departures").select_all({"tour_id": "tour-uuid-1"})
        assert len(stored) == 3

    def test_idempotent_second_run_does_not_duplicate(self, supabase):
        rows = self._parsed()
        first = upsert_departure_rows(rows, supabase=supabase, tour_id="tour-uuid-1")
        second = upsert_departure_rows(rows, supabase=supabase, tour_id="tour-uuid-1")
        assert first.inserted == 3
        assert second.inserted == 0
        assert second.updated == 3  # all three already existed
        stored = supabase.table("tour_departures").select_all({"tour_id": "tour-uuid-1"})
        assert len(stored) == 3  # still 3, never 6

    def test_missing_dates_are_skipped_not_inserted_as_null(self, supabase):
        rows = [
            DeparturePriceRow(web_code="ap111111"),  # no date
            DeparturePriceRow(
                web_code="ap111111",
                departure_start=date(2026, 6, 18),
                departure_end=date(2026, 6, 23),
                adult_price=10000,
            ),
        ]
        result = upsert_departure_rows(rows, supabase=supabase, tour_id="tour-uuid-x")
        assert result.upserted == 1
        assert result.skipped_no_date == 1
        stored = supabase.table("tour_departures").select_all({"tour_id": "tour-uuid-x"})
        assert len(stored) == 1
        assert stored[0]["departure_start"] == "2026-06-18"

    def test_dash_cells_stay_null_in_persisted_payload(self, supabase):
        rows = self._parsed()
        upsert_departure_rows(rows, supabase=supabase, tour_id="tour-uuid-1")
        stored = supabase.table("tour_departures").select_all({"tour_id": "tour-uuid-1"})
        # Row 2: child_bed = "-", child_no_bed = "-"
        row_2 = next(r for r in stored if r["departure_start"] == "2026-07-29")
        assert row_2["child_bed_price"] is None
        assert row_2["child_no_bed_price"] is None
        # CRITICAL: never coerced to 0
        for k in (
            "adult_price",
            "child_bed_price",
            "child_no_bed_price",
            "single_supplement_price",
            "joinland_price",
        ):
            v = row_2[k]
            assert v is None or v > 0

    def test_codes_kept_separate_on_persisted_row(self, supabase):
        rows = self._parsed()
        upsert_departure_rows(rows, supabase=supabase, tour_id="tour-uuid-1")
        stored = supabase.table("tour_departures").select_all({"tour_id": "tour-uuid-1"})
        r = stored[0]
        assert r["web_code"] == "ap242455"
        assert r["tour_code_real"] == "BCCKG27-HU"
        assert r["airline"] == "HU"
        # Mutual inequality (the three fields can never collapse)
        assert r["web_code"] != r["tour_code_real"]
        assert r["tour_code_real"] != r["airline"]
        assert r["web_code"] != r["airline"]

    def test_contact_button_status_never_persists_as_sold_out(self, supabase):
        rows = self._parsed()
        upsert_departure_rows(rows, supabase=supabase, tour_id="tour-uuid-1")
        stored = supabase.table("tour_departures").select_all({"tour_id": "tour-uuid-1"})
        contact_rows = [
            r for r in stored if r["status_text"] == "ติดต่อเจ้าหน้าที่"
        ]
        assert contact_rows, "test data should include contact-button rows"
        for r in contact_rows:
            assert r["availability_status"] != "sold_out"
            # The legacy mirror "status" defaults to "available" — never sold_out
            assert r["status"] != "sold_out"

    def test_sold_out_row_classified_from_class_signal(self, supabase):
        rows = self._parsed()
        upsert_departure_rows(rows, supabase=supabase, tour_id="tour-uuid-1")
        stored = supabase.table("tour_departures").select_all({"tour_id": "tour-uuid-1"})
        sold = [r for r in stored if r["availability_status"] == "sold_out"]
        assert len(sold) == 1
        assert sold[0]["status_text"] == "เต็ม"

    def test_legacy_field_mirroring_persisted(self, supabase):
        rows = self._parsed()
        upsert_departure_rows(rows, supabase=supabase, tour_id="tour-uuid-1")
        stored = supabase.table("tour_departures").select_all({"tour_id": "tour-uuid-1"})
        r = next(x for x in stored if x["departure_start"] == "2026-06-18")
        # Legacy mirrors
        assert r["departure_date"] == r["departure_start"]
        assert r["return_date"] == r["departure_end"]
        assert r["price"] == r["adult_price"]

    def test_idempotency_keys_are_unique_across_rows(self, supabase):
        rows = self._parsed()
        result = upsert_departure_rows(rows, supabase=supabase, tour_id="tour-uuid-1")
        # 3 parsed rows → 3 distinct keys
        assert len(result.idempotency_keys) == 3
        assert len(set(result.idempotency_keys)) == 3
        # Shape: (id, start, end, bus)
        first = result.idempotency_keys[0]
        assert first == ("tour-uuid-1", "2026-06-18", "2026-06-23", 1)

    def test_no_tour_id_falls_back_to_web_code_match(self, supabase):
        rows = self._parsed()
        # First insert without tour_id
        first = upsert_departure_rows(rows, supabase=supabase, tour_id=None)
        # Re-run — should update existing rows, not duplicate them
        second = upsert_departure_rows(rows, supabase=supabase, tour_id=None)
        assert first.inserted == 3
        assert second.inserted == 0
        assert second.updated == 3
        stored = supabase.table("tour_departures").select_all({"web_code": "ap242455"})
        assert len(stored) == 3


# ---------------------------------------------------------------------------
# enrich_tour_detail — top-level orchestration
# ---------------------------------------------------------------------------


class TestEnrichTourDetail:
    def test_happy_path_fetches_parses_and_persists(self, supabase):
        http = FakeHttp()
        result = enrich_tour_detail(
            "ap242455",
            http=http,
            supabase=supabase,
            tour_id="tour-uuid-1",
        )
        assert result.fetched is True
        assert result.parsed is True
        assert result.persisted is True
        assert result.error is None
        assert len(result.rows) == 3
        assert result.header["tour_code_real"] == "BCCKG27-HU"
        assert result.header["airline"] == "HU"
        assert result.header["web_code"] == "ap242455"
        # Detail URL on every row
        assert all("/tour/ap242455" in (r.source_url or "") for r in result.rows)
        assert all("/intertourdetail/" not in (r.source_url or "") for r in result.rows)

        # Persisted shape
        assert result.persistence is not None
        assert result.persistence.upserted == 3
        stored = supabase.table("tour_departures").select_all({"tour_id": "tour-uuid-1"})
        assert len(stored) == 3

    def test_fetch_failure_returns_clean_result_no_partial_write(self, supabase):
        http = RaisingHttp()
        result = enrich_tour_detail(
            "ap242455",
            http=http,
            supabase=supabase,
            tour_id="tour-uuid-1",
        )
        assert result.fetched is False
        assert result.parsed is False
        assert result.persisted is False
        assert result.error == "fetch_failed_or_non200"
        assert result.rows == []
        # Nothing written
        stored = supabase.table("tour_departures").select_all({"tour_id": "tour-uuid-1"})
        assert stored == []

    def test_persist_disabled_runs_parser_but_skips_db(self, supabase):
        http = FakeHttp()
        result = enrich_tour_detail(
            "ap242455",
            http=http,
            supabase=supabase,
            tour_id="tour-uuid-1",
            persist=False,
        )
        assert result.fetched is True
        assert result.parsed is True
        assert result.persisted is False
        assert result.persistence is None
        # No DB writes
        stored = supabase.table("tour_departures").select_all({"tour_id": "tour-uuid-1"})
        assert stored == []

    def test_persist_no_supabase_runs_parser_but_skips_db(self):
        http = FakeHttp()
        result = enrich_tour_detail("ap242455", http=http, supabase=None)
        assert result.fetched is True
        assert result.parsed is True
        assert result.persisted is False
        assert result.persistence is None
        assert len(result.rows) == 3

    def test_idempotent_second_enrichment(self, supabase):
        http = FakeHttp()
        first = enrich_tour_detail(
            "ap242455",
            http=http,
            supabase=supabase,
            tour_id="tour-uuid-1",
        )
        second = enrich_tour_detail(
            "ap242455",
            http=http,
            supabase=supabase,
            tour_id="tour-uuid-1",
        )
        assert first.persistence.inserted == 3
        assert second.persistence.inserted == 0
        assert second.persistence.updated == 3
        stored = supabase.table("tour_departures").select_all({"tour_id": "tour-uuid-1"})
        # Idempotent — still 3, not 6.
        assert len(stored) == 3

    def test_summary_dict_is_admin_safe(self, supabase):
        http = FakeHttp()
        result = enrich_tour_detail(
            "ap242455",
            http=http,
            supabase=supabase,
            tour_id="tour-uuid-1",
        )
        summary = result.to_summary()
        assert summary["web_code"] == "ap242455"
        assert summary["row_count"] == 3
        assert summary["upserted"] == 3
        assert summary["fetched"] is True
        assert summary["persisted"] is True
        assert "/tour/ap242455" in summary["source_url"]
        # Contains no secret fields — only safe keys
        for k in summary:
            assert "token" not in k.lower()
            assert "secret" not in k.lower()
            assert "psid" not in k.lower()

    def test_empty_web_code_returns_error_result(self, supabase):
        http = FakeHttp()
        result = enrich_tour_detail("", http=http, supabase=supabase)
        assert result.fetched is False
        assert result.parsed is False
        assert result.error == "missing_web_code"
        # No fetch attempted
        assert http.calls == []

    def test_no_live_network_during_unit_run(self, supabase):
        """Belt-and-braces: the only HTTP allowed is our fake."""
        http = FakeHttp()
        enrich_tour_detail("ap242455", http=http, supabase=supabase, tour_id="t1")
        # Each call URL is our local fake — nothing escapes the process.
        for url, _t in http.calls:
            assert url.startswith(BASE_URL + "/tour/")
