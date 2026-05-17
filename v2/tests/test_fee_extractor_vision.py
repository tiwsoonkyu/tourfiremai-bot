"""Sprint 3 test: LLM vision extractor — mock client only."""

import os
import pytest

from v2.scraper.extract_fees import llm_vision_extract, _result_from_dict
from v2.lib.llm import MockLLMClient


def test_result_from_dict():
    data = {
        "tip_amount": 1500, "visa_fee": 2000,
        "single_supplement": 5500, "deposit_amount": 10000,
        "infant_fee": None, "child_fee_no_bed": None,
        "extraction_confidence": 0.85, "notes": "from vision",
    }
    r = _result_from_dict(data, method="llm_vision")
    assert r.tip_amount == 1500
    assert r.extraction_method == "llm_vision"
    assert r.extraction_confidence == 0.85


def test_vision_skipped_when_pdf2image_missing(tmp_path):
    """If pdf2image isn't installed, vision should return error rather than crash."""
    # Create a real but empty file (just to pass os.path.exists)
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    llm = MockLLMClient()
    # If pdf2image is installed, this test will succeed differently
    result = llm_vision_extract(str(pdf), llm)
    # Either pdf2image_missing OR pdf2image_error — both are acceptable
    # We mainly verify it doesn't crash + returns ExtractionResult
    assert result.extraction_method == "llm_vision"


def test_vision_with_mock_returns_skeleton():
    """Direct mock-vision call. Used by extractor when text layer fails."""
    llm = MockLLMClient()
    # Vision client uses fake bytes
    rsp = llm.vision(messages=[{"role": "user", "content": "extract"}],
                      image_bytes=b"\x89PNG_fake")
    assert rsp.structured is not None
    assert "tip_amount" in rsp.structured
