"""
Sprint 5 Package F (DEV-2026-05-20-012)
Tests for v2.scraper.departure_price_table.

No live network. No LLM. No DB. Pure parsing tests against synthetic HTML
fixtures that mirror the live tourfiremai.com/tour/<web_code> structure.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from v2.scraper.departure_price_table import (
    DeparturePriceRow,
    _classify_availability,
    _parse_int_or_none,
    _parse_money_or_none,
    idempotency_key,
    parse_departure_price_table,
    parse_detail_header_codes,
    parse_thai_date_range,
    to_tour_departure_rows,
)


# ---------------------------------------------------------------------------
# Fixture: a stripped-down version of a real detail-page price table.
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
# Date parsing
# ---------------------------------------------------------------------------


class TestParseThaiDateRange:
    def test_same_month_with_be_year_suffix(self):
        start, end = parse_thai_date_range("18-23 มิ.ย. 69")
        assert start == date(2026, 6, 18)
        assert end == date(2026, 6, 23)

    def test_cross_month_with_be_year_suffix(self):
        start, end = parse_thai_date_range("29 ก.ค. - 4 ส.ค. 69")
        assert start == date(2026, 7, 29)
        assert end == date(2026, 8, 4)

    def test_cross_year_with_be_year_suffix(self):
        # 29 ธ.ค. 68 → 4 ม.ค. 69
        start, end = parse_thai_date_range("29 ธ.ค. - 4 ม.ค. 69")
        assert start == date(2025, 12, 29)
        assert end == date(2026, 1, 4)

    def test_full_two_sided_range_with_month_and_year_on_both_sides(self):
        # Live pages sometimes repeat month/year on both sides:
        # "04 มิ.ย. 69 - 08 มิ.ย. 69".
        start, end = parse_thai_date_range("04 มิ.ย. 69 - 08 มิ.ย. 69")
        assert start == date(2026, 6, 4)
        assert end == date(2026, 6, 8)

    def test_full_two_sided_cross_year_range(self):
        start, end = parse_thai_date_range("29 ธ.ค. 68 - 04 ม.ค. 69")
        assert start == date(2025, 12, 29)
        assert end == date(2026, 1, 4)

    def test_single_date(self):
        start, end = parse_thai_date_range("5 ก.ค. 69")
        assert start == date(2026, 7, 5)
        assert end == date(2026, 7, 5)

    def test_may_abbreviation(self):
        start, end = parse_thai_date_range("12-17 พ.ค. 69")
        assert start == date(2026, 5, 12)
        assert end == date(2026, 5, 17)

    @pytest.mark.parametrize(
        "label,start_iso,end_iso",
        [
            ("3-7 มิ.ย. 69", "2026-06-03", "2026-06-07"),
            ("1 ก.ค. 69", "2026-07-01", "2026-07-01"),
            ("28-31 ก.ค. 69", "2026-07-28", "2026-07-31"),
            ("30 ก.ค. - 5 ส.ค. 69", "2026-07-30", "2026-08-05"),
        ],
    )
    def test_examples(self, label, start_iso, end_iso):
        start, end = parse_thai_date_range(label)
        assert start.isoformat() == start_iso
        assert end.isoformat() == end_iso

    def test_unparseable(self):
        assert parse_thai_date_range("see brochure") == (None, None)

    def test_empty(self):
        assert parse_thai_date_range("") == (None, None)

    def test_full_year_be(self):
        start, end = parse_thai_date_range("18-23 มิ.ย. 2569")
        assert start == date(2026, 6, 18)
        assert end == date(2026, 6, 23)


# ---------------------------------------------------------------------------
# Money / int cell parsing
# ---------------------------------------------------------------------------


class TestParseMoneyOrNone:
    def test_thousands_with_comma(self):
        assert _parse_money_or_none("25,900") == 25900

    def test_bare_thousands(self):
        assert _parse_money_or_none("9990") == 9990

    @pytest.mark.parametrize("tok", ["-", "", "–", "—", "_", "N/A", "ไม่มี"])
    def test_missing_tokens_return_none_not_zero(self, tok):
        assert _parse_money_or_none(tok) is None

    def test_contact_button_returns_none(self):
        assert _parse_money_or_none("ติดต่อเจ้าหน้าที่") is None

    def test_strips_currency_words(self):
        assert _parse_money_or_none("25,900 บาท") == 25900

    def test_rejects_zero(self):
        assert _parse_money_or_none("0") is None


class TestParseIntOrNone:
    def test_simple(self):
        assert _parse_int_or_none("30") == 30

    def test_dash_returns_none(self):
        assert _parse_int_or_none("-") is None

    def test_empty_returns_none(self):
        assert _parse_int_or_none("") is None

    def test_zero_returns_none(self):
        assert _parse_int_or_none("0") is None


# ---------------------------------------------------------------------------
# Availability classification (sold-out caution)
# ---------------------------------------------------------------------------


class TestClassifyAvailability:
    def test_contact_button_is_unknown_not_sold_out(self):
        # CRITICAL: generic contact copy must never become "sold_out".
        assert _classify_availability("b-tb-dp", "s-tb9-n", "ติดต่อเจ้าหน้าที่") == "unknown"

    def test_sold_out_class_on_row_wins(self):
        assert (
            _classify_availability("b-tb-dp row-soldout", "s-tb9-n", "ติดต่อเจ้าหน้าที่")
            == "sold_out"
        )

    def test_explicit_full_text_via_class(self):
        assert _classify_availability("b-tb-dp full", "s-tb9-n", "เต็ม") == "sold_out"

    def test_explicit_available_text(self):
        assert _classify_availability("b-tb-dp", "s-tb9-n", "ว่าง 20 ที่นั่ง") == "available"

    def test_empty_text_is_unknown(self):
        assert _classify_availability("b-tb-dp", "s-tb9-n", "") == "unknown"

    def test_dash_text_is_unknown(self):
        assert _classify_availability("b-tb-dp", "s-tb9-n", "-") == "unknown"


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------


class TestParseDetailHeaderCodes:
    def test_extracts_tour_code_real_airline_web_code_separately(self):
        out = parse_detail_header_codes(FIXTURE_DETAIL_HTML)
        assert out["tour_code_real"] == "BCCKG27-HU"
        assert out["airline"] == "HU"
        assert out["web_code"] == "ap242455"
        # CRITICAL: these three never collapse into each other.
        assert out["tour_code_real"] != out["web_code"]
        assert out["tour_code_real"] != out["airline"]
        assert out["web_code"] != out["airline"]

    def test_empty_html(self):
        assert parse_detail_header_codes("") == {
            "tour_code_real": None,
            "airline": None,
            "web_code": None,
        }

    def test_codepg_without_airline_still_parses_real_code(self):
        html = '<div class="b-codepg">BCCKG27-HU</div>'
        out = parse_detail_header_codes(html)
        assert out["tour_code_real"] == "BCCKG27-HU"
        # Airline mirrored from the -HU suffix because there's no other token.
        assert out["airline"] == "HU"

    def test_live_codepg_value_shape_prefers_actual_value_not_label_or_title(self):
        html = """
        <html>
          <head><meta property="og:title" content="DOUBLE FREEDAY TOKYO"></head>
          <body>
            <div class="txt-dt-icon b-codepg">
              <p><span class="icon"></span> รหัสทัวร์</p>
              <p class="txt-pd-l">BT-NRT_S15_XJ</p>
            </div>
            <div class="txt-dt-icon"><p>เดินทางโดย</p><p>Thai AirAsia X</p></div>
            <a href="https://www.tourfiremai.com/tour/ap232919">ดูโปรแกรม</a>
          </body>
        </html>
        """
        out = parse_detail_header_codes(html)
        assert out["tour_code_real"] == "BT-NRT_S15_XJ"
        assert out["airline"] == "XJ"
        assert out["web_code"] == "ap232919"
        # Regression guard: the page title contains an uppercase sales word
        # and must never be mistaken for the real tour code.
        assert out["tour_code_real"] != "DOUBLE"

    def test_live_codepg_value_shape_accepts_no_dash_real_code(self):
        html = """
        <div class="txt-dt-icon b-codepg">
          <p>รหัสทัวร์</p>
          <p class="txt-pd-l">TFUEU0626</p>
        </div>
        <a href="/tour/ap183598">รายละเอียด</a>
        """
        out = parse_detail_header_codes(html)
        assert out["tour_code_real"] == "TFUEU0626"
        assert out["web_code"] == "ap183598"


# ---------------------------------------------------------------------------
# Full price-table parsing
# ---------------------------------------------------------------------------


class TestParseDeparturePriceTable:
    def test_parses_three_rows_from_fixture(self):
        rows = parse_departure_price_table(
            FIXTURE_DETAIL_HTML, "ap242455", source_url="https://example/ap242455"
        )
        assert len(rows) == 3
        assert all(isinstance(r, DeparturePriceRow) for r in rows)

    def test_first_row_full_field_extraction(self):
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, "ap242455")
        r = rows[0]
        assert r.web_code == "ap242455"
        assert r.tour_code_real == "BCCKG27-HU"
        assert r.airline == "HU"
        assert r.departure_start == date(2026, 6, 18)
        assert r.departure_end == date(2026, 6, 23)
        assert r.departure_label_raw == "18-23 มิ.ย. 69"
        assert r.bus == 1
        assert r.adult_price == 25900
        assert r.child_bed_price == 24900
        assert r.child_no_bed_price == 23900
        assert r.single_supplement_price == 5500
        # joinland was "-"
        assert r.joinland_price is None
        assert r.group_size == 30
        # Status preserved verbatim, NOT interpreted as sold-out.
        assert r.status_text == "ติดต่อเจ้าหน้าที่"
        assert r.availability_status == "unknown"

    def test_dash_cells_yield_none_not_zero(self):
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, "ap242455")
        # Row 2: child_bed="-", child_no_bed="-"
        r = rows[1]
        assert r.child_bed_price is None
        assert r.child_no_bed_price is None
        # Make sure nothing snuck a 0 in
        for f in (
            "adult_price",
            "child_bed_price",
            "child_no_bed_price",
            "single_supplement_price",
            "joinland_price",
        ):
            assert getattr(r, f) != 0

    def test_row_with_soldout_class_is_classified(self):
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, "ap242455")
        r = rows[2]
        assert r.availability_status == "sold_out"
        assert r.status_text == "เต็ม"

    def test_contact_button_status_never_becomes_sold_out(self):
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, "ap242455")
        for r in rows[:2]:  # first two rows have contact button
            assert r.status_text == "ติดต่อเจ้าหน้าที่"
            assert r.availability_status != "sold_out"

    def test_web_code_lowercase(self):
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, "AP242455")
        assert all(r.web_code == "ap242455" for r in rows)

    def test_source_url_default_uses_tour_path_not_intertourdetail(self):
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, "ap242455")
        assert all("/tour/" in (r.source_url or "") for r in rows)
        assert all("/intertourdetail/" not in (r.source_url or "") for r in rows)

    def test_empty_html(self):
        assert parse_departure_price_table("", "ap242455") == []

    def test_no_table_no_rows(self):
        assert parse_departure_price_table("<html><body>nope</body></html>", "ap242455") == []

    def test_cross_month_row_parses(self):
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, "ap242455")
        r = rows[1]
        assert r.departure_start == date(2026, 7, 29)
        assert r.departure_end == date(2026, 8, 4)


# ---------------------------------------------------------------------------
# tour_departures adapter + idempotency key
# ---------------------------------------------------------------------------


class TestToTourDepartureRows:
    def test_legacy_field_mirroring(self):
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, "ap242455")
        payloads = to_tour_departure_rows(rows, tour_id="tour-uuid-1")
        p = payloads[0]
        # New columns
        assert p["adult_price"] == 25900
        assert p["single_supplement_price"] == 5500
        assert p["departure_start"] == "2026-06-18"
        assert p["departure_end"] == "2026-06-23"
        # Legacy mirrors
        assert p["price"] == 25900
        assert p["departure_date"] == "2026-06-18"
        assert p["return_date"] == "2026-06-23"
        # No silent zero injection on missing fields
        assert p["joinland_price"] is None
        # tour_id propagates
        assert p["tour_id"] == "tour-uuid-1"
        # codes never mixed
        assert p["web_code"] == "ap242455"
        assert p["tour_code_real"] == "BCCKG27-HU"
        assert p["airline"] == "HU"

    def test_missing_prices_stay_null_in_payload(self):
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, "ap242455")
        payloads = to_tour_departure_rows(rows)
        for p in payloads:
            for k in (
                "adult_price",
                "child_bed_price",
                "child_no_bed_price",
                "single_supplement_price",
                "joinland_price",
            ):
                assert p[k] is None or p[k] > 0

    def test_skips_rows_with_no_date(self):
        bad = DeparturePriceRow(web_code="ap111111")
        good = DeparturePriceRow(
            web_code="ap111111",
            departure_start=date(2026, 6, 18),
            departure_end=date(2026, 6, 23),
            adult_price=10000,
        )
        out = to_tour_departure_rows([bad, good])
        assert len(out) == 1
        assert out[0]["departure_start"] == "2026-06-18"

    def test_idempotency_key_uses_tour_id_then_dates_then_bus(self):
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, "ap242455")
        payloads = to_tour_departure_rows(rows, tour_id="tour-uuid-1")
        keys = [idempotency_key(p, tour_id="tour-uuid-1") for p in payloads]
        # No duplicates across the three rows
        assert len(set(keys)) == len(keys)
        # Shape: (id, start, end, bus)
        assert keys[0] == ("tour-uuid-1", "2026-06-18", "2026-06-23", 1)

    def test_status_mirror_defaults_to_available_when_unknown(self):
        rows = parse_departure_price_table(FIXTURE_DETAIL_HTML, "ap242455")
        payloads = to_tour_departure_rows(rows)
        # First two rows are "unknown" → legacy "available" (cautious default)
        assert payloads[0]["status"] == "available"
        assert payloads[1]["status"] == "available"
        # Third row was sold_out
        assert payloads[2]["status"] == "sold_out"


# ---------------------------------------------------------------------------
# Migration 021 — SQL file shape
# ---------------------------------------------------------------------------


MIGRATION_021_PATH = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "migrations"
    / "20260520_021_departure_price_rows.sql"
)


class TestMigration021Shape:
    @pytest.fixture(scope="class")
    def sql(self) -> str:
        assert MIGRATION_021_PATH.exists(), f"missing {MIGRATION_021_PATH}"
        return MIGRATION_021_PATH.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "col",
        [
            "departure_start",
            "departure_end",
            "departure_label_raw",
            "bus",
            "adult_price",
            "child_bed_price",
            "child_no_bed_price",
            "single_supplement_price",
            "joinland_price",
            "group_size",
            "status_text",
            "status_class",
            "availability_status",
            "source_url",
            "tour_code_real",
        ],
    )
    def test_adds_column_idempotently(self, sql, col):
        # Each new column must be added with ADD COLUMN IF NOT EXISTS.
        snippet = f"ADD COLUMN IF NOT EXISTS {col}"
        assert snippet in sql, f"migration 021 missing additive column: {col}"

    def test_does_not_drop_or_rename(self, sql):
        assert "DROP COLUMN" not in sql.upper()
        assert "RENAME COLUMN" not in sql.upper()
        assert "DROP TABLE" not in sql.upper()

    def test_does_not_truncate(self, sql):
        assert "TRUNCATE" not in sql.upper()

    def test_does_not_force_default_zero_on_prices(self, sql):
        # Never coerce missing money to 0 via DEFAULT 0 on price columns.
        for col in (
            "adult_price",
            "child_bed_price",
            "child_no_bed_price",
            "single_supplement_price",
            "joinland_price",
        ):
            assert f"{col} INTEGER DEFAULT 0" not in sql
            assert f"{col} INTEGER NOT NULL" not in sql

    def test_availability_status_check_constraint_present(self, sql):
        assert "availability_status" in sql
        assert "available" in sql
        assert "sold_out" in sql
        assert "unknown" in sql

    def test_backfill_keeps_legacy_compatible(self, sql):
        # Mirrors specified in CURRENT_DEV_TASK.md
        assert "SET departure_start = departure_date" in sql
        assert "SET departure_end = return_date" in sql
        assert "SET adult_price = price" in sql
