"""Sprint 3 test: regex-based fee extractor (no PDF needed)."""

import pytest
from v2.scraper.extract_fees import regex_extract, _parse_number, ExtractionResult


class TestParseNumber:
    @pytest.mark.parametrize("raw,expected", [
        ("1,500", 1500),
        ("1500", 1500),
        ("25,900", 25900),
        ("0", 0),
        ("999", 999),
        ("1,000,000", 1000000),
    ])
    def test_valid(self, raw, expected):
        assert _parse_number(raw) == expected

    @pytest.mark.parametrize("raw", ["abc", "", "1.5", "99999999"])
    def test_invalid(self, raw):
        assert _parse_number(raw) is None


class TestRegexThaiPatterns:
    def test_full_text_all_fields_match(self):
        text = (
            "ค่าทิปไกด์และคนขับ 1,500 บาท / ท่าน\n"
            "ค่าวีซ่าจีน 1,650 บาท\n"
            "พักเดี่ยวเพิ่ม 5,500 บาท\n"
            "ค่ามัดจำ 10,000 บาท\n"
        )
        r = regex_extract(text)
        assert r.tip_amount == 1500
        assert r.visa_fee == 1650
        assert r.single_supplement == 5500
        assert r.deposit_amount == 10000
        assert r.is_complete is True
        assert r.extraction_confidence >= 0.9

    def test_partial_text(self):
        text = "ค่าทิป 1,000 บาท\nค่ามัดจำ 5,000 บาท"
        r = regex_extract(text)
        assert r.tip_amount == 1000
        assert r.deposit_amount == 5000
        assert r.visa_fee is None
        assert r.single_supplement is None
        assert r.is_complete is False
        # Confidence reflects partial match
        assert 0.3 < r.extraction_confidence < 0.7

    def test_infant_fee(self):
        # Simple form without intervening "2 ปี" digit (regex tolerance limit)
        text = "ทารก 4,500 บาท"
        r = regex_extract(text)
        assert r.infant_fee == 4500

    def test_infant_fee_english(self):
        text = "infant 4500"
        r = regex_extract(text)
        assert r.infant_fee == 4500

    def test_empty_text(self):
        r = regex_extract("")
        assert r.tip_amount is None
        assert r.extraction_confidence == 0
        assert "empty_text_input" in r.extraction_errors

    def test_english_tip(self):
        # Phase 2 follow-up: regex now REQUIRES บาท/baht suffix for
        # money-critical fields to prevent price-table column false positives.
        # Real wholesale PDFs always include the suffix; the test fixture
        # is updated to match.
        text = "tip 1500 baht\nvisa 2000 baht\nsingle supplement 3000 baht\ndeposit 8000 baht"
        r = regex_extract(text)
        assert r.tip_amount == 1500
        assert r.visa_fee == 2000
        assert r.single_supplement == 3000
        assert r.deposit_amount == 8000


class TestExtractionResult:
    def test_is_complete_requires_all_4(self):
        r = ExtractionResult(
            tip_amount=1500, visa_fee=2000, single_supplement=3000,
            deposit_amount=8000,
        )
        assert r.is_complete is True

    def test_is_complete_false_if_missing(self):
        r = ExtractionResult(tip_amount=1500, visa_fee=None,
                              single_supplement=3000, deposit_amount=8000)
        assert r.is_complete is False

    def test_to_db_row(self):
        r = ExtractionResult(tip_amount=1500, extraction_method="test")
        row = r.to_db_row(tour_id="UUID", tour_code_real="X-Y",
                          pdf_url="https://x", pdf_hash="abc")
        assert row["tour_id"] == "UUID"
        assert row["tip_amount"] == 1500
        assert row["manually_verified"] is False


class TestNewFieldsR2:
    def test_visa_status_exempt(self):
        from v2.scraper.extract_fees import regex_extract
        r = regex_extract("ลูกค้าไม่ต้องวีซ่า (visa free) สำหรับไทย")
        assert r.visa_status == "exempt"

    def test_visa_status_on_arrival(self):
        from v2.scraper.extract_fees import regex_extract
        r = regex_extract("วีซ่า on arrival 30 USD")
        assert r.visa_status == "on_arrival"

    def test_joinland_price(self):
        from v2.scraper.extract_fees import regex_extract
        r = regex_extract("Joinland 15,900 บาท / ท่าน")
        assert r.joinland_price == 15900

    def test_raw_snippet_captured(self):
        from v2.scraper.extract_fees import regex_extract
        text = "Some preamble " * 50 + "ค่าทิป 1500 บาท" + " trailing text" * 50
        r = regex_extract(text, source_page=2)
        assert r.raw_snippet is not None
        assert "1500" in r.raw_snippet or "ทิป" in r.raw_snippet
        assert r.source_page == 2

    def test_completeness_requires_visa(self):
        from v2.scraper.extract_fees import ExtractionResult
        # All numeric fields but no visa decision → NOT complete
        r = ExtractionResult(
            tip_amount=1500, single_supplement=5500, deposit_amount=10000,
        )
        assert r.is_complete is False
        # Adding visa_status='exempt' completes it (visa_fee unnecessary)
        r.visa_status = "exempt"
        assert r.is_complete is True
