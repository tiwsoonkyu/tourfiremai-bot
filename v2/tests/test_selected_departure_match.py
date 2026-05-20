"""
Sprint 5 Package G (DEV-2026-05-20-013).

Tests for v2.lib.selected_departure_match — deterministic matcher used by
the orchestrator/response writer to pick the exact departure row for a
selected tour given a customer's date phrase.

Hard rules under test:
    - LLM is never the source of truth for selected-row matching.
    - When the customer phrase is ambiguous, the matcher returns
      ``ambiguous`` rather than guessing.
    - When the customer phrase parses to a date but no row matches,
      the matcher returns ``no_match`` with a descriptive error.
    - web_code, tour_code_real, and airline never collapse on the match.
    - "-" / missing prices on the source row stay None on the match.
    - Generic contact-button rows surface availability_status="unknown"
      (never "sold_out") through the matcher.
    - Past dates are explicitly rejected — never quoted as if open.
"""

from __future__ import annotations

from datetime import date

import pytest

from v2.lib.selected_departure_match import (
    DepartureMatchResult,
    list_available_departures,
    match_departure,
    parse_customer_date_phrase,
)
from v2.scraper.departure_price_table import (
    DeparturePriceRow,
    parse_departure_price_table,
)


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


@pytest.fixture
def rows() -> list[DeparturePriceRow]:
    return parse_departure_price_table(FIXTURE_DETAIL_HTML, "ap242455")


# ---------------------------------------------------------------------------
# parse_customer_date_phrase
# ---------------------------------------------------------------------------


class TestParseCustomerDatePhrase:
    def test_single_day_phrase(self):
        # "13 มิ.ย. 3 คน" — the "3 คน" is unrelated traveller count
        # but the date is unambiguous.
        d = parse_customer_date_phrase("13 มิ.ย. 3 คน", today=date(2026, 5, 1))
        assert d == date(2026, 6, 13)

    def test_range_phrase_returns_start(self):
        d = parse_customer_date_phrase(
            "18-23 มิ.ย. 69 ขอราคา", today=date(2026, 5, 1)
        )
        assert d == date(2026, 6, 18)

    def test_unparseable_returns_none(self):
        assert parse_customer_date_phrase("เอาทัวร์เลย") is None

    def test_empty_returns_none(self):
        assert parse_customer_date_phrase("") is None


# ---------------------------------------------------------------------------
# match_departure — exact start match
# ---------------------------------------------------------------------------


class TestMatchDepartureExact:
    def test_exact_start_date_high_confidence(self, rows):
        # "18 มิ.ย. 69" matches the first row's start exactly.
        result = match_departure(rows, "18 มิ.ย. 69", today=date(2026, 5, 1))
        assert result.status == "matched"
        assert result.match is not None
        assert result.match.confidence == "high"
        assert result.match.departure_start == date(2026, 6, 18)
        assert result.match.departure_end == date(2026, 6, 23)
        assert result.match.adult_price == 25900
        # Codes preserved separately
        assert result.match.web_code == "ap242455"
        assert result.match.tour_code_real == "BCCKG27-HU"
        assert result.match.airline == "HU"
        assert result.match.web_code != result.match.tour_code_real
        assert result.match.tour_code_real != result.match.airline

    def test_exact_start_date_for_cross_month_row(self, rows):
        result = match_departure(rows, "29 ก.ค. 69", today=date(2026, 5, 1))
        assert result.status == "matched"
        assert result.match.departure_start == date(2026, 7, 29)
        assert result.match.departure_end == date(2026, 8, 4)
        assert result.match.confidence == "high"


# ---------------------------------------------------------------------------
# match_departure — in-range (medium confidence)
# ---------------------------------------------------------------------------


class TestMatchDepartureInRange:
    def test_date_inside_single_row_range_returns_medium(self, rows):
        # "20 มิ.ย." is inside 18-23 มิ.ย. but not the start.
        result = match_departure(rows, "20 มิ.ย. 69", today=date(2026, 5, 1))
        assert result.status == "matched"
        assert result.match.confidence == "medium"
        assert result.match.departure_start == date(2026, 6, 18)


# ---------------------------------------------------------------------------
# match_departure — no match / past date / unparseable
# ---------------------------------------------------------------------------


class TestMatchDepartureNoMatch:
    def test_date_not_in_any_row(self, rows):
        result = match_departure(rows, "1 ก.ย. 69", today=date(2026, 5, 1))
        assert result.status == "no_match"
        assert result.error == "date_not_in_any_row"
        assert result.match is None

    def test_past_date_rejected(self, rows):
        # Customer asks about 18 ม.ค. 69 (Jan) but "today" is May 1, 2026.
        result = match_departure(rows, "18 ม.ค. 69", today=date(2026, 5, 1))
        assert result.status == "no_match"
        assert result.error == "date_in_past"
        # We still report the parsed date so callers can echo it back.
        assert result.parsed_phrase_date == date(2026, 1, 18)

    def test_unparseable_phrase(self, rows):
        result = match_departure(rows, "ขอคุยเล่นๆ", today=date(2026, 5, 1))
        assert result.status == "unparseable"
        assert result.error == "no_date_in_phrase"
        assert result.match is None

    def test_no_rows_returns_no_match(self):
        result = match_departure([], "20 มิ.ย. 69", today=date(2026, 5, 1))
        assert result.status == "no_match"
        assert result.error == "no_rows_with_dates"

    def test_does_not_guess_low_confidence_by_default(self, rows):
        # "17 มิ.ย." is one day before the 18-23 มิ.ย. range. By default
        # the matcher refuses to guess.
        result = match_departure(rows, "17 มิ.ย. 69", today=date(2026, 5, 1))
        assert result.status == "no_match"
        assert result.match is None

    def test_low_confidence_opt_in_returns_low(self, rows):
        result = match_departure(
            rows,
            "17 มิ.ย. 69",
            today=date(2026, 5, 1),
            allow_low_confidence=True,
        )
        assert result.status == "matched"
        assert result.match is not None
        assert result.match.confidence == "low"
        assert result.match.departure_start == date(2026, 6, 18)


# ---------------------------------------------------------------------------
# match_departure — ambiguity is explicit, never a guess
# ---------------------------------------------------------------------------


class TestMatchDepartureAmbiguous:
    def test_multiple_rows_with_same_start_returns_ambiguous(self):
        # Synthetic: two rows starting on the same day, different bus
        a = DeparturePriceRow(
            web_code="ap242455",
            departure_start=date(2026, 6, 18),
            departure_end=date(2026, 6, 23),
            adult_price=25900,
            bus=1,
        )
        b = DeparturePriceRow(
            web_code="ap242455",
            departure_start=date(2026, 6, 18),
            departure_end=date(2026, 6, 23),
            adult_price=26900,
            bus=2,
        )
        result = match_departure([a, b], "18 มิ.ย. 69", today=date(2026, 5, 1))
        assert result.status == "ambiguous"
        assert result.match is None
        assert len(result.candidates) == 2
        assert result.error == "multiple_rows_share_start_date"

    def test_overlapping_in_range_returns_ambiguous(self):
        # Two rows whose date ranges overlap on the queried date — ambiguous.
        a = DeparturePriceRow(
            web_code="ap242455",
            departure_start=date(2026, 6, 18),
            departure_end=date(2026, 6, 23),
            adult_price=25900,
        )
        b = DeparturePriceRow(
            web_code="ap242455",
            departure_start=date(2026, 6, 20),
            departure_end=date(2026, 6, 25),
            adult_price=26900,
        )
        result = match_departure([a, b], "21 มิ.ย. 69", today=date(2026, 5, 1))
        assert result.status == "ambiguous"
        assert len(result.candidates) == 2
        assert result.error == "multiple_rows_contain_date"


# ---------------------------------------------------------------------------
# match_departure — generic contact-button row never becomes sold_out
# ---------------------------------------------------------------------------


class TestContactButtonNeverSoldOut:
    def test_match_preserves_unknown_availability_for_contact_rows(self, rows):
        result = match_departure(rows, "18 มิ.ย. 69", today=date(2026, 5, 1))
        assert result.status == "matched"
        assert result.match.availability_status == "unknown"
        assert result.match.status_text == "ติดต่อเจ้าหน้าที่"

    def test_sold_out_row_carries_through_match(self, rows):
        # "5 ก.ค. 69" is the sold-out row in the fixture.
        result = match_departure(rows, "5 ก.ค. 69", today=date(2026, 5, 1))
        assert result.status == "matched"
        assert result.match.availability_status == "sold_out"
        assert result.match.status_text == "เต็ม"


# ---------------------------------------------------------------------------
# Null-stays-null on the match (no zero coercion)
# ---------------------------------------------------------------------------


class TestNoZeroCoercionOnMatch:
    def test_dash_cells_become_none_on_match(self, rows):
        # Row 2 (cross-month) has child_bed = "-" and child_no_bed = "-".
        result = match_departure(rows, "30 ก.ค. 69", today=date(2026, 5, 1))
        assert result.status == "matched"
        assert result.match.child_bed_price is None
        assert result.match.child_no_bed_price is None
        # CRITICAL: never coerced to 0
        for v in (
            result.match.adult_price,
            result.match.child_bed_price,
            result.match.child_no_bed_price,
            result.match.single_supplement_price,
            result.match.joinland_price,
        ):
            assert v is None or v > 0


# ---------------------------------------------------------------------------
# list_available_departures helper
# ---------------------------------------------------------------------------


class TestListAvailableDepartures:
    def test_excludes_sold_out(self, rows):
        listing = list_available_departures(rows, today=date(2026, 5, 1))
        statuses = [m.availability_status for m in listing]
        assert "sold_out" not in statuses
        # 3 rows in fixture, 1 is sold_out → 2 returned.
        assert len(listing) == 2

    def test_excludes_past_rows(self, rows):
        # If we move "today" past 18 มิ.ย. 69 only the cross-month row remains.
        listing = list_available_departures(rows, today=date(2026, 7, 1))
        assert all(
            m.departure_start is None or m.departure_start >= date(2026, 7, 1)
            for m in listing
        )

    def test_sorted_by_start_date(self, rows):
        listing = list_available_departures(rows, today=date(2026, 5, 1))
        starts = [m.departure_start for m in listing]
        assert starts == sorted(starts)

    def test_limit_caps_output(self, rows):
        listing = list_available_departures(rows, today=date(2026, 5, 1), limit=1)
        assert len(listing) == 1


# ---------------------------------------------------------------------------
# Round-trip: DepartureMatchResult.to_dict is JSON-friendly
# ---------------------------------------------------------------------------


class TestResultToDict:
    def test_to_dict_serializes_dates(self, rows):
        result = match_departure(rows, "18 มิ.ย. 69", today=date(2026, 5, 1))
        d = result.to_dict()
        assert d["status"] == "matched"
        assert d["match"]["departure_start"] == "2026-06-18"
        assert d["match"]["departure_end"] == "2026-06-23"
        assert d["parsed_phrase_date"] == "2026-06-18"

    def test_no_match_dict_shape(self, rows):
        result = match_departure(rows, "ขอราคา", today=date(2026, 5, 1))
        d = result.to_dict()
        assert d["status"] == "unparseable"
        assert d["match"] is None
        assert d["candidates"] == []
        assert d["error"] == "no_date_in_phrase"

    def test_does_not_use_llm_or_network(self, rows):
        # The result dict must be derivable from already-parsed rows; this
        # is a structural assertion that this module does not depend on a
        # network or LLM client.
        import v2.lib.selected_departure_match as m

        # Source file must not import requests or any LLM client at module level.
        import inspect

        src = inspect.getsource(m)
        for forbidden in ("import requests", "openai", "anthropic", "supabase"):
            assert forbidden not in src
