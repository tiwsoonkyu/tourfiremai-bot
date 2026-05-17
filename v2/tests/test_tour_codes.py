"""Sprint 1 test: tour code separation + airline guard."""

import pytest
from v2.lib.tour_codes import (
    normalize_web_code,
    normalize_tour_code_real,
    parse_airline,
    ensure_airline_not_used_as_code,
    classify_code,
    CodeMisuseError,
)


class TestWebCode:
    @pytest.mark.parametrize("text,expected", [
        ("ap242455", "ap242455"),
        ("AP242455", "ap242455"),
        ("  ap242455  ", "ap242455"),
        ("ลูกค้าอยากดู ap242455 อันนี้", "ap242455"),
        ("in123456", "in123456"),
    ])
    def test_valid_web_codes(self, text, expected):
        assert normalize_web_code(text) == expected

    @pytest.mark.parametrize("text", [
        "BCCKG27-HU",   # tour_code_real format
        "HU",           # airline
        "12345",        # no prefix
        "",
        None,
        "ap999",        # too short
    ])
    def test_invalid_web_codes(self, text):
        assert normalize_web_code(text) is None


class TestTourCodeReal:
    @pytest.mark.parametrize("text,expected", [
        ("BCCKG27-HU", "BCCKG27-HU"),
        ("bcckg27-hu", "BCCKG27-HU"),
        ("รหัสจริง BCCKG27-HU ครับ", "BCCKG27-HU"),
        ("JX001", "JX001"),
    ])
    def test_valid_real_codes(self, text, expected):
        assert normalize_tour_code_real(text) == expected

    @pytest.mark.parametrize("text", [
        "ap242455",   # web_code, not tour_code_real
        "HU",         # airline alone
        "TG",         # airline alone
        "",
        None,
    ])
    def test_invalid_real_codes(self, text):
        assert normalize_tour_code_real(text) is None

    def test_airline_not_returned_as_real_code(self):
        # Critical: "HU" must not be classified as tour_code_real
        assert normalize_tour_code_real("HU") is None


class TestAirline:
    @pytest.mark.parametrize("text,expected", [
        ("TG", "TG"),
        ("Tg", "TG"),
        ("airline = HU", "HU"),
        ("BCCKG27-HU", "HU"),
        ("XJ", "XJ"),
    ])
    def test_known_airlines(self, text, expected):
        assert parse_airline(text) == expected

    @pytest.mark.parametrize("text", [
        "XX",        # not a known airline
        "ap242455",  # web_code
        "",
        None,
    ])
    def test_unknown_airlines(self, text):
        assert parse_airline(text) is None


class TestAirlineNotUsedAsCode:
    def test_airline_eq_code_raises(self):
        with pytest.raises(CodeMisuseError):
            ensure_airline_not_used_as_code("HU", "HU")

    def test_different_codes_ok(self):
        ensure_airline_not_used_as_code("HU", "BCCKG27-HU")  # no raise

    def test_none_inputs_ok(self):
        ensure_airline_not_used_as_code(None, "BCCKG27-HU")
        ensure_airline_not_used_as_code("HU", None)
        ensure_airline_not_used_as_code(None, None)


class TestClassify:
    @pytest.mark.parametrize("text,kind,value", [
        ("ap242455", "web", "ap242455"),
        ("BCCKG27-HU", "tour_code_real", "BCCKG27-HU"),
        ("TG", "airline", "TG"),
        ("HU", "airline", "HU"),
        ("blah blah", None, None),
    ])
    def test_classify(self, text, kind, value):
        result = classify_code(text)
        assert result["kind"] == kind
        assert result["value"] == value
