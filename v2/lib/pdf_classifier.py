"""
v2.lib.pdf_classifier — Detect PDF type: text / scanned / mixed.

Used by fee pipeline step 4 to decide which extraction path to take:
  - text:    Layer 2 (regex) → Layer 3 (LLM text) only
  - scanned: Layer 4 (LLM vision) only
  - mixed:   per-page decision

Heuristic: extract text per page; pages with < MIN_TEXT_CHARS chars OR no
fee-keyword hits are flagged as "image-only". If all pages have text → text;
if no pages have text → scanned; otherwise → mixed.

Public API:
    classify_pdf(pdf_path) -> PdfClassification
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("v2.pdf_classifier")


MIN_TEXT_CHARS_PER_PAGE = 80
# Thai + English fee keywords (used to decide which pages have fee content)
_FEE_KEYWORDS_RE = re.compile(
    r"(ค่าทิป|ทิป|tip|วีซ่า|visa|พักเดี่ยว|single|มัดจำ|deposit|"
    r"ทารก|infant|เด็ก.*?(?:เตียง)?|child|joinland|join\s*land|"
    r"land\s*tour|อัตราค่าบริการ|ค่าใช้จ่ายเพิ่มเติม)",
    re.I,
)


@dataclass
class PageInfo:
    page_no: int            # 1-indexed
    char_count: int
    has_text: bool          # text extracted normally
    has_fee_keyword: bool   # at least one fee-related keyword found


@dataclass
class PdfClassification:
    kind: str              # 'text' | 'scanned' | 'mixed' | 'empty'
    total_pages: int
    text_pages: int        # pages with sufficient text
    scanned_pages: list[int]  # 1-indexed pages that need vision
    fee_pages: list[int]      # pages where fee content was detected
    pages: list[PageInfo] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def is_text(self) -> bool: return self.kind == "text"
    @property
    def is_scanned(self) -> bool: return self.kind == "scanned"
    @property
    def is_mixed(self) -> bool: return self.kind == "mixed"

    @property
    def needs_vision(self) -> bool:
        """True if at least one page needs vision (scanned/mixed/no-keyword)."""
        return bool(self.scanned_pages)


def classify_pdf(pdf_path: str) -> PdfClassification:
    """
    Inspect a PDF and decide which extraction path to use.

    Uses pdfplumber if available; degrades gracefully when not.
    """
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return PdfClassification(
            kind="empty", total_pages=0, text_pages=0,
            scanned_pages=[], fee_pages=[],
            error="pdfplumber_not_installed",
        )

    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages_info: list[PageInfo] = []
            for i, page in enumerate(pdf.pages, start=1):
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""
                cc = len(text.strip())
                has_text = cc >= MIN_TEXT_CHARS_PER_PAGE
                has_kw = bool(_FEE_KEYWORDS_RE.search(text)) if text else False
                pages_info.append(PageInfo(
                    page_no=i, char_count=cc,
                    has_text=has_text, has_fee_keyword=has_kw,
                ))
    except Exception as e:
        return PdfClassification(
            kind="empty", total_pages=0, text_pages=0,
            scanned_pages=[], fee_pages=[],
            error=f"pdfplumber_error: {type(e).__name__}: {e}",
        )

    total = len(pages_info)
    if total == 0:
        return PdfClassification(
            kind="empty", total_pages=0, text_pages=0,
            scanned_pages=[], fee_pages=[], pages=[],
        )

    text_count = sum(1 for p in pages_info if p.has_text)
    # Scanned-flag candidates: low text OR has text but no fee keyword anywhere
    # in document (suggests boilerplate without fee schedule on this page)
    scanned_pages = [p.page_no for p in pages_info if not p.has_text]
    fee_pages = [p.page_no for p in pages_info if p.has_fee_keyword]

    # Decide kind
    if text_count == total:
        kind = "text"
    elif text_count == 0:
        kind = "scanned"
    else:
        kind = "mixed"

    return PdfClassification(
        kind=kind,
        total_pages=total,
        text_pages=text_count,
        scanned_pages=scanned_pages,
        fee_pages=fee_pages,
        pages=pages_info,
    )
