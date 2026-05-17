"""Sprint 3 test: pdfplumber + LLM-text extraction path (mock LLM only)."""

import os
import pytest

# Try imports — skip if deps not installed
pdfplumber = pytest.importorskip("pdfplumber")
reportlab_check = pytest.importorskip("reportlab.pdfgen.canvas")


from v2.scraper.extract_fees import extract_text_from_pdf, llm_text_extract, extract_fees
from v2.lib.llm import MockLLMClient
from v2.tests.fixtures.synthetic_pdf import build_synthetic_fee_pdf


@pytest.fixture
def synthetic_pdf(tmp_path):
    path = str(tmp_path / "synthetic_fee.pdf")
    return build_synthetic_fee_pdf(path)


class TestPdfPlumberLayer:
    def test_extracts_text_from_synthetic_pdf(self, synthetic_pdf):
        text = extract_text_from_pdf(synthetic_pdf)
        # Synthetic PDF uses English by default (Thai font not available);
        # accept either Thai or English keywords.
        assert any(kw in text.lower() for kw in ("ค่าทิป", "ทิป", "tip", "visa"))


class TestLLMTextLayer:
    def test_calls_llm_with_text(self):
        llm = MockLLMClient()
        result = llm_text_extract("ค่าทิป 1500 บาท", llm)
        # MockLLMClient returns skeleton vision JSON for fast tier — but check method label
        assert result.extraction_method == "llm_text"

    def test_empty_text_returns_error(self):
        llm = MockLLMClient()
        result = llm_text_extract("", llm)
        assert result.extraction_method == "llm_text"
        assert "empty_text_input" in result.extraction_errors


class TestFullLadder:
    def test_synthetic_pdf_extracts_via_regex(self, synthetic_pdf):
        llm = MockLLMClient()
        result = extract_fees(synthetic_pdf, llm, skip_vision=True)
        # Synthetic PDF has all 4 required fields → regex should hit ≥3 → return regex result
        assert result.extraction_method in ("pdfplumber+regex", "llm_text")
        # At minimum some field should be found
        any_filled = any(getattr(result, f) is not None for f in
                         ("tip_amount", "visa_fee", "single_supplement", "deposit_amount"))
        assert any_filled

    def test_missing_pdf_returns_error(self):
        llm = MockLLMClient()
        result = extract_fees("/tmp/does_not_exist.pdf", llm, skip_vision=True)
        assert result.extraction_method == "none"
        assert any("not_found" in e for e in result.extraction_errors)
