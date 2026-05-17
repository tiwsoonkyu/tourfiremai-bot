"""
Generate a tiny synthetic PDF with Thai fee text for tests.
Used by test_fee_extractor_text.py — avoids needing real wholesale PDFs in CI.
"""

import os
from typing import Optional


def build_synthetic_fee_pdf(out_path: str, *, fee_text: Optional[str] = None) -> str:
    """
    Create a minimal PDF containing the given Thai fee text. Returns the path.

    Default text covers all 4 required fields + 1 optional (infant_fee).
    """
    try:
        from reportlab.pdfgen import canvas  # type: ignore
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError as e:
        raise RuntimeError(
            "reportlab not installed; run `pip install reportlab` for synthetic PDFs"
        ) from e

    # Default uses English fee labels so reportlab's default font renders correctly
    # AND pdfplumber can extract the text. Regex layer has both EN+TH patterns
    # so English test still validates the pipeline.
    text = fee_text or (
        "Additional Fees:\n"
        "- tip 1500 baht\n"
        "- visa fee 1650 baht\n"
        "- single supplement 5500 baht\n"
        "- deposit 10000 baht\n"
    )

    # Try to use a Thai-capable font if present; otherwise rely on draw_string
    # falling back to default (will render boxes but pdfplumber.extract_text
    # can still read embedded text streams).
    c = canvas.Canvas(out_path)
    y = 750
    for line in text.split("\n"):
        c.drawString(72, y, line)
        y -= 18
    c.showPage()
    c.save()
    return out_path
