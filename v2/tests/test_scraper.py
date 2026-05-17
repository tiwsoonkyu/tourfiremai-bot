"""Sprint 1 test: scraper HTML parsing (no network)."""

import pytest
from v2.scraper.scrape_tours import (
    parse_listing_html, parse_price, parse_days_nights,
    parse_airline, parse_departure_dates,
)
from datetime import date


SAMPLE_LISTING_HTML = """
<html><body>
<div class="tour-card">
  <a href="/intertourdetail/ap242455">
    <h3>ทัวร์ญี่ปุ่นโตเกียว ฟูจิ ออนเซ็น 5 วัน 4 คืน</h3>
  </a>
  <div class="info">
    <span>ราคา 25,900 บาท</span>
    <span>5 วัน 4 คืน</span>
    <span>บินกับ HU</span>
    <span>เดินทาง 18-23 มิ.ย. 69</span>
  </div>
</div>
<div class="tour-card">
  <a href="/intertourdetail/ap333333">
    <h3>ทัวร์ญี่ปุ่นโอซาก้า เกียวโต 6 วัน 5 คืน</h3>
  </a>
  <span>ราคา 32,900</span>
  <span>6 วัน 5 คืน</span>
  <span>VZ</span>
  <span>5 ก.ค. 69</span>
</div>
<div class="banner">ทัวร์ไฟไหม้ Flash Sale!</div>
<a href="/intertourdetail/ap555555">
  <h3>ทัวร์ ฮอกไกโด คิวชู</h3>
</a>
<span>9,990</span>
<span>4 วัน 3 คืน</span>
<span>XJ</span>
</body></html>
"""


class TestParsePrice:
    def test_thousands_with_comma(self):
        assert parse_price("ราคา 25,900 บาท") == 25900

    def test_thousands_without_comma(self):
        assert parse_price("4500 บาท") == 4500

    def test_no_price(self):
        assert parse_price("ลูกค้าน่ารัก") == 0

    def test_ignores_below_threshold(self):
        # Smaller than 1000 should not match as price
        assert parse_price("999") == 0


class TestParseDaysNights:
    def test_normal(self):
        assert parse_days_nights("5 วัน 4 คืน") == (5, 4)

    def test_no_match(self):
        assert parse_days_nights("hello") == (0, 0)


class TestParseAirline:
    @pytest.mark.parametrize("text,expected", [
        ("บินกับ HU", "HU"),
        ("vz", "VZ"),
        ("airline=TG", "TG"),
    ])
    def test_match(self, text, expected):
        assert parse_airline(text) == expected

    def test_no_match(self):
        assert parse_airline("ไม่มี") is None


class TestParseDepartureDates:
    def test_thai_range(self):
        dates = parse_departure_dates("เดินทาง 18-23 มิ.ย. 69")
        # Should produce 2 endpoints (start + end)
        iso = sorted(d.isoformat() for d in dates)
        assert "2026-06-18" in iso
        assert "2026-06-23" in iso

    def test_thai_single(self):
        dates = parse_departure_dates("5 ก.ค. 69")
        assert any(d == date(2026, 7, 5) for d in dates)

    def test_empty(self):
        assert parse_departure_dates("") == []


class TestParseListingHtml:
    def test_finds_three_tours(self):
        tours = parse_listing_html(SAMPLE_LISTING_HTML, country="ญี่ปุ่น", country_id=2)
        codes = sorted(t.web_code for t in tours)
        # Each web_code appears multiple times in the HTML; dedup expected
        assert codes == ["ap242455", "ap333333", "ap555555"]

    def test_keeps_web_code_and_airline_separate(self):
        tours = parse_listing_html(SAMPLE_LISTING_HTML, country="ญี่ปุ่น", country_id=2)
        for t in tours:
            assert t.web_code.startswith("ap")
            assert t.airline is None or len(t.airline) <= 4
            # Airline must never equal web_code
            assert t.web_code != t.airline

    def test_price_parsed(self):
        tours = parse_listing_html(SAMPLE_LISTING_HTML, country="ญี่ปุ่น", country_id=2)
        # Map web_code → price
        prices = {t.web_code: t.base_price for t in tours}
        assert prices["ap242455"] == 25900
        assert prices["ap333333"] == 32900
        assert prices["ap555555"] == 9990

    def test_airline_extracted(self):
        tours = parse_listing_html(SAMPLE_LISTING_HTML, country="ญี่ปุ่น", country_id=2)
        airlines = {t.web_code: t.airline for t in tours}
        assert airlines["ap242455"] == "HU"
        assert airlines["ap333333"] == "VZ"
        assert airlines["ap555555"] == "XJ"

    def test_empty_html(self):
        assert parse_listing_html("", "x", 0) == []

    def test_no_tour_links(self):
        assert parse_listing_html("<html><body>hello</body></html>", "x", 0) == []


class TestUpsertIntegration:
    def test_upsert_writes_to_supabase(self, supabase):
        from v2.scraper.scrape_tours import upsert_tours_to_canonical, ParsedTour
        from datetime import date
        parsed = [
            ParsedTour(
                web_code="ap242455", name="Test Tour",
                country="ญี่ปุ่น", country_id=2,
                days=5, nights=4, base_price=25900, airline="HU",
                url="https://x", departure_dates=[date(2026, 6, 18)],
            )
        ]
        result = upsert_tours_to_canonical(parsed, supabase)
        assert result["upserted"] == 1
        assert result["departures_inserted"] == 1
        assert not result["errors"]

        # Verify in store
        row = supabase.table("tours_canonical").select_one({"web_code": "ap242455"})
        assert row["name"] == "Test Tour"
        assert row["airline"] == "HU"
        # Confirm separation
        assert row["airline"] != row["web_code"]
