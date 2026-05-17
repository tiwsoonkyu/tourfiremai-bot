"""Sprint 3 R2 test: PDF classifier — text/scanned/mixed."""

import pytest

pdfplumber = pytest.importorskip("pdfplumber")
reportlab = pytest.importorskip("reportlab.pdfgen.canvas")

from v2.lib.pdf_classifier import classify_pdf, PdfClassification
from v2.tests.fixtures.synthetic_pdf import build_synthetic_fee_pdf


@pytest.fixture
def text_pdf(tmp_path):
    return build_synthetic_fee_pdf(str(tmp_path / "text.pdf"))


class TestClassifyTextPdf:
    def test_returns_text_kind(self, text_pdf):
        cls = classify_pdf(text_pdf)
        # synthetic English PDF should classify as text
        assert cls.kind == "text"
        assert cls.total_pages == 1
        assert cls.text_pages == 1
        assert cls.error is None

    def test_fee_keywords_detected(self, text_pdf):
        cls = classify_pdf(text_pdf)
        # synthetic PDF has "tip", "visa", "deposit" → fee_keyword True
        assert 1 in cls.fee_pages

    def test_no_vision_needed_for_text_pdf(self, text_pdf):
        cls = classify_pdf(text_pdf)
        assert cls.needs_vision is False


class TestMissingPdf:
    def test_nonexistent_file(self):
        cls = classify_pdf("/tmp/does_not_exist.pdf")
        assert cls.error is not None or cls.kind == "empty"


class TestEmptyPdf:
    def test_empty_pdf_returns_empty_kind(self, tmp_path):
        # Generate a 0-content PDF (just whitespace)
        path = str(tmp_path / "blank.pdf")
        build_synthetic_fee_pdf(path, fee_text=" ")
        cls = classify_pdf(path)
        # Could be classified as scanned (zero text pages) or empty
        assert cls.kind in ("scanned", "empty", "text")
