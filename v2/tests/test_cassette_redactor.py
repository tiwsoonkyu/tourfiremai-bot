"""Sprint 4 test: cassette_redactor masks sensitive data while preserving fee numerics."""

import pytest
from v2.lib.cassette_redactor import (
    redact_cassette, _mask_wholesale_in_text, _truncate_raw_snippet,
    _anonymize_tour_code,
)


class TestMaskWholesale:
    @pytest.mark.parametrize("text", [
        "ทัวร์ของ TTN",
        "Best Tour group",
        "Zego ตัวแทน",
        "I-Travel ส่งโปร",
        "Rich Tour partner",
        "Formosa ส่ง PDF",
        "ลูกค้าซื้อจาก GS Travel",
    ])
    def test_wholesale_masked(self, text):
        out = _mask_wholesale_in_text(text)
        assert "WS_***" in out

    @pytest.mark.parametrize("text", [
        "ทัวร์โตเกียวน่าสนใจ",
        "check in 14.00 น.",
        "tags: japan, korea",
        "the best in town",     # 'best' without 'tour' should NOT match
    ])
    def test_innocent_preserved(self, text):
        out = _mask_wholesale_in_text(text)
        assert out == text  # no change


class TestTruncateRawSnippet:
    def test_short_unchanged(self):
        assert _truncate_raw_snippet("short text") == "short text"

    def test_long_truncated(self):
        long = "x" * 600
        out = _truncate_raw_snippet(long)
        assert len(out) == 500
        assert out.endswith("...")

    def test_non_string_passthrough(self):
        assert _truncate_raw_snippet(None) is None
        assert _truncate_raw_snippet(123) == 123


class TestAnonymizeTourCode:
    def test_stable_mapping(self):
        mapping: dict[str, str] = {}
        a = _anonymize_tour_code("รหัส BCCKG27-HU", mapping)
        assert "BCCKG27-HU" not in a
        # Same original → same replacement
        b = _anonymize_tour_code("ดูรหัส BCCKG27-HU อีกที", mapping)
        # Both should contain the same WS{N}-{NNN} string
        ws_in_a = [tok for tok in a.split() if tok.startswith("WS")]
        ws_in_b = [tok for tok in b.split() if tok.startswith("WS")]
        assert ws_in_a == ws_in_b

    def test_no_match_passthrough(self):
        mapping: dict[str, str] = {}
        out = _anonymize_tour_code("just normal text", mapping)
        assert out == "just normal text"


class TestRedactCassette:
    def test_pii_masked(self):
        cassette = {
            "request": {"messages": [{"role": "user", "content": "psid=1234567890123456 email=a@b.com"}]},
            "response": {"text": "ok", "usage": {}},
        }
        out = redact_cassette(cassette)
        flat = str(out)
        assert "a@b.com" not in flat
        assert "1234567890123456" not in flat

    def test_wholesale_masked_in_messages(self):
        cassette = {
            "request": {"messages": [{"role": "user", "content": "นำมาจาก TTN partner"}]},
            "response": {"text": "ok", "usage": {}},
        }
        out = redact_cassette(cassette)
        assert "TTN" not in str(out)

    def test_raw_snippet_truncated_in_response(self):
        long = "x" * 800
        cassette = {
            "request": {"messages": [{"role": "user", "content": "x"}]},
            "response": {
                "text": "ok",
                "structured": {"raw_snippet": long, "tip_amount": 1500},
                "usage": {},
            },
        }
        out = redact_cassette(cassette)
        snippet = out["response"]["structured"]["raw_snippet"]
        assert len(snippet) <= 500

    def test_numeric_fees_preserved(self):
        cassette = {
            "request": {"messages": [{"role": "user", "content": "x"}]},
            "response": {
                "text": "ok",
                "structured": {"tip_amount": 1500, "deposit_amount": 10000,
                                 "single_supplement": 5500},
                "usage": {},
            },
        }
        out = redact_cassette(cassette)
        assert out["response"]["structured"]["tip_amount"] == 1500
        assert out["response"]["structured"]["deposit_amount"] == 10000

    def test_does_not_mutate_input(self):
        original = {"request": {"messages": [{"role": "user", "content": "TTN"}]},
                    "response": {"text": "ok", "usage": {}}}
        redact_cassette(original)
        # Original retained
        assert "TTN" in str(original)
