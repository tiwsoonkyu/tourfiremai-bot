"""
v2.scraper.extract_fees — Layered fee extractor for tour PDFs.

4-layer extraction ladder:
    Layer 1: pdfplumber → raw text
    Layer 2: regex pattern match on text
    Layer 3: LLM (fast tier) parses text → structured JSON
    Layer 4: LLM vision on PDF page image → structured JSON

Sprint 3 deliverable. LLM client is injectable for mock/cassette/live modes.

Public API:
    extract_fees(pdf_path: str, llm: LLMClient) -> ExtractionResult

The result is meant to be upserted into the `tour_fees` table via the
orchestrator or a cron job — this module does NOT touch the DB.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..lib.llm import LLMClient
from ..lib.fee_schema import build_response_format

logger = logging.getLogger("v2.scraper.fee_extractor")


# --- Regex patterns (Thai-first; English fallback) ----------------------------

_NUMBER_RE = r"([1-9]\d{0,2}(?:,\d{3})+|\d{2,6})"  # e.g. "1,500" or "1500"

# Visa status keywords (Thai + English)
_VISA_STATUS_PATTERNS = [
    (re.compile(r"(?:ไม่ต้อง|ไม่ใช้|ฟรี\s*วีซ่า|visa\s*free|no\s*visa|exempt)", re.I), "exempt"),
    (re.compile(r"(?:วีซ่า\s*ออน\s*อะ?ไรเวล|on\s*arrival|voa)", re.I), "on_arrival"),
    (re.compile(r"\be[-\s]?visa\b", re.I), "evisa"),
    (re.compile(r"(ต้องมีวีซ่า|วีซ่า\s*[1-9])", re.I), "required"),
]

# Sprint 4 Phase 2 follow-up: money-critical fields now REQUIRE a "บาท"/"baht"
# suffix within ~6 chars of the captured number. This stops the regex from
# latching onto column values in price tables (e.g. "ห้องพักเดี่ยว ท่าน 19 – 23
# มิถุนายน 2569 19,990 ..." used to extract `19` or `19,990` for
# single_supplement — both wrong).
_BAHT_TAIL = r"\s*(?:บาท|baht)"

_PATTERNS: dict[str, list[re.Pattern]] = {
    "tip_amount": [
        # "ค่าทิป ... 2,000 บาท" (canonical)
        re.compile(rf"ค่าทิป[^0-9]{{0,80}}{_NUMBER_RE}{_BAHT_TAIL}", re.I),
        # "ทิปไกด์/คนขับ ... ท่านละ 2,000 บาท"
        re.compile(rf"ทิป(?:ไกด์|คนขับ|หัวหน้าทัวร์)?[^0-9]{{0,80}}{_NUMBER_RE}{_BAHT_TAIL}", re.I),
        re.compile(rf"\btip\b[^0-9]{{0,40}}{_NUMBER_RE}{_BAHT_TAIL}", re.I),
    ],
    "visa_fee": [
        re.compile(rf"(?:ค่า)?วีซ่า[^0-9]{{0,40}}{_NUMBER_RE}{_BAHT_TAIL}", re.I),
        re.compile(rf"\bvisa\b[^0-9]{{0,30}}{_NUMBER_RE}{_BAHT_TAIL}", re.I),
    ],
    "single_supplement": [
        # "พักเดี่ยวเพิ่ม 5,500 บาท" / "single supplement 5,500 baht"
        # NB: does NOT match "ห้องพักเดี่ยว ท่าน 19 – 23..." because no บาท follows
        # the next number; price-table columns are caught by vision instead.
        re.compile(rf"(?<!ห้อง)พักเดี่ยว(?:เพิ่ม)?[^0-9]{{0,40}}{_NUMBER_RE}{_BAHT_TAIL}", re.I),
        re.compile(rf"single\s*supp(?:lement)?[^0-9]{{0,40}}{_NUMBER_RE}\s*(?:บาท|baht)", re.I),
    ],
    "deposit_amount": [
        # "ชำระเงินมัดจำ ท่านละ 15,000 บาท" / "deposit 10,000 baht"
        re.compile(rf"(?:ค่า|ชำระเงิน|วาง)?มัดจำ[^0-9]{{0,40}}{_NUMBER_RE}{_BAHT_TAIL}", re.I),
        re.compile(rf"\bdeposit\b[^0-9]{{0,30}}{_NUMBER_RE}\s*(?:บาท|baht)", re.I),
    ],
    "infant_fee": [
        # "(Infant) = 9,900.-" or "ทารก ... ท่านละ 6,000 บาท"
        re.compile(rf"ทารก[^0-9]{{0,80}}{_NUMBER_RE}{_BAHT_TAIL}", re.I),
        re.compile(rf"\(?infant\)?[^0-9]{{0,40}}{_NUMBER_RE}\s*(?:บาท|\.\-|baht)?", re.I),
    ],
    "child_fee_no_bed": [
        re.compile(rf"เด็ก(?:ไม่)?(?:มี)?เสริมเตียง[^0-9]{{0,30}}{_NUMBER_RE}{_BAHT_TAIL}?", re.I),
        re.compile(rf"no\s*bed[^0-9]{{0,30}}{_NUMBER_RE}", re.I),
    ],
    "joinland_price": [
        # joinland is rarely บาท-suffixed in real PDFs; keep loose match.
        re.compile(rf"join[-\s]?land[^0-9]{{0,30}}{_NUMBER_RE}", re.I),
        re.compile(rf"land\s*tour\s*(?:price|ราคา)?[^0-9]{{0,30}}{_NUMBER_RE}", re.I),
        re.compile(rf"jointour[^0-9]{{0,30}}{_NUMBER_RE}", re.I),
    ],
}

# Required fields for "complete" status. visa_fee=null is OK if visa_status='exempt'.
_REQUIRED_FIELDS = {"tip_amount", "single_supplement", "deposit_amount"}


@dataclass
class ExtractionResult:
    # Core fee fields (Sprint 3 R2 brief)
    tip_amount: Optional[int] = None           # alias: tip_fee
    visa_fee: Optional[int] = None
    visa_status: Optional[str] = None          # 'exempt'|'required'|'on_arrival'|'evisa'|'unknown'|None
    single_supplement: Optional[int] = None
    infant_fee: Optional[int] = None
    child_fee_no_bed: Optional[int] = None     # alias: child_no_bed_fee
    deposit_amount: Optional[int] = None       # alias: deposit
    joinland_price: Optional[int] = None       # land-only price (no flights)
    mandatory_fees_summary: Optional[str] = None  # human-readable summary

    # Extraction provenance
    extraction_method: str = "none"           # 'pdfplumber+regex' | 'llm_text' | 'llm_vision' | 'manual'
    extraction_confidence: float = 0.0         # alias: fee_confidence
    extraction_errors: list[str] = field(default_factory=list)
    notes: str = ""
    source_page: Optional[int] = None          # 1-indexed page where fees found
    raw_snippet: Optional[str] = None          # ~500-char window around fee region

    # Sprint 4 follow-up: per-field confidence (None = fall back to extraction_confidence)
    tip_confidence: Optional[float] = None
    deposit_confidence: Optional[float] = None
    single_supplement_confidence: Optional[float] = None
    visa_confidence: Optional[float] = None

    @property
    def is_complete(self) -> bool:
        # All required numeric fields present + visa decided
        required_ok = all(
            getattr(self, f) is not None and getattr(self, f) >= 0
            for f in _REQUIRED_FIELDS
        )
        visa_decided = self.visa_fee is not None or self.visa_status in (
            "exempt", "required", "on_arrival", "evisa"
        )
        return required_ok and visa_decided

    def to_db_row(self, *, tour_id: str, tour_code_real: str, pdf_url: str,
                  pdf_hash: str) -> dict:
        return {
            "tour_id": tour_id,
            "tour_code_real": tour_code_real,
            "pdf_url": pdf_url,
            "pdf_hash": pdf_hash,
            "tip_amount": self.tip_amount,
            "visa_fee": self.visa_fee,
            "visa_status": self.visa_status,
            "single_supplement": self.single_supplement,
            "infant_fee": self.infant_fee,
            "child_fee_no_bed": self.child_fee_no_bed,
            "deposit_amount": self.deposit_amount,
            "joinland_price": self.joinland_price,
            "mandatory_fees_summary": self.mandatory_fees_summary,
            "extraction_method": self.extraction_method,
            "extraction_confidence": self.extraction_confidence,
            "extraction_errors": self.extraction_errors,
            "source_page": self.source_page,
            "raw_snippet": self.raw_snippet,
            "tip_confidence": self.tip_confidence,
            "deposit_confidence": self.deposit_confidence,
            "single_supplement_confidence": self.single_supplement_confidence,
            "visa_confidence": self.visa_confidence,
            "manually_verified": False,
        }


# --- Layer 1: pdfplumber text ------------------------------------------------

def extract_text_from_pdf(pdf_path: str) -> str:
    """Layer 1. Returns concatenated text from all pages. Empty string on fail."""
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        logger.warning("pdfplumber not installed; text layer skipped")
        return ""

    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages_text = []
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text.strip():
                    pages_text.append(text)
            return "\n\n".join(pages_text)
    except Exception as e:
        logger.warning("pdfplumber failed on %s: %s", pdf_path, e)
        return ""


# --- Layer 2: regex ----------------------------------------------------------

def _parse_number(raw: str) -> Optional[int]:
    if not raw:
        return None
    s = raw.replace(",", "").strip()
    try:
        n = int(s)
        if 0 <= n <= 1_000_000:
            return n
    except ValueError:
        return None
    return None


def regex_extract(text: str, *, source_page: Optional[int] = None) -> ExtractionResult:
    """Layer 2. Pure regex; no LLM cost.

    Args:
        text: extracted PDF text (single page or whole document)
        source_page: 1-indexed page number where this text came from
    """
    result = ExtractionResult(extraction_method="pdfplumber+regex", source_page=source_page)
    if not text or not text.strip():
        result.extraction_errors.append("empty_text_input")
        return result

    matched = 0
    earliest_match_start: Optional[int] = None
    for field_name, patterns in _PATTERNS.items():
        for pat in patterns:
            m = pat.search(text)
            if m:
                val = _parse_number(m.group(1))
                if val is not None:
                    setattr(result, field_name, val)
                    matched += 1
                    # Track earliest fee match for raw_snippet window
                    if earliest_match_start is None or m.start() < earliest_match_start:
                        earliest_match_start = m.start()
                    break

    # Visa status detection (separate from visa_fee)
    for pat, status in _VISA_STATUS_PATTERNS:
        if pat.search(text):
            result.visa_status = status
            break

    # raw_snippet — ±250 char window around earliest fee match
    if earliest_match_start is not None:
        start = max(0, earliest_match_start - 200)
        end = min(len(text), earliest_match_start + 300)
        result.raw_snippet = text[start:end].strip()

    # Confidence: required-field coverage, weighted
    required_hits = sum(1 for f in _REQUIRED_FIELDS if getattr(result, f) is not None)
    # Visa decided counts toward confidence too
    visa_ok = (result.visa_fee is not None) or (result.visa_status is not None)
    completion = (required_hits + (1 if visa_ok else 0)) / (len(_REQUIRED_FIELDS) + 1)
    result.extraction_confidence = round(min(0.95, completion) * 0.95, 2)

    # Per-field confidence — Phase 2 follow-up: now that regex REQUIRES a
    # "บาท"/"baht" suffix on the money-critical fields, a regex hit is highly
    # reliable (no more table-column false positives). We can therefore raise
    # the baselines above the policy thresholds:
    #   tip / deposit / visa → 0.85  (policy threshold 0.80 ⇒ regex alone answers)
    #   single_supplement     → 0.82 (policy threshold 0.90 ⇒ vision still required
    #                                  for the final lift, but regex no longer
    #                                  forces handoff with a *wrong* 0.60)
    # The take-max vision bump from the wire-in commit still lifts these to
    # 0.92+ when a vision call confirms the value.
    if result.tip_amount is not None:
        result.tip_confidence = 0.85
    if result.deposit_amount is not None:
        result.deposit_confidence = 0.85
    if result.single_supplement is not None:
        result.single_supplement_confidence = 0.82
    if result.visa_status is not None or result.visa_fee is not None:
        result.visa_confidence = 0.85

    result.notes = f"regex matched {matched} fields ({required_hits}/{len(_REQUIRED_FIELDS)} required, visa={result.visa_status})"
    return result


# --- Layer 3: LLM on text ----------------------------------------------------

def llm_text_extract(text: str, llm: LLMClient) -> ExtractionResult:
    """Layer 3. LLM (fast tier) parses text → structured JSON."""
    if not text or not text.strip():
        return ExtractionResult(extraction_method="llm_text",
                                 extraction_errors=["empty_text_input"])

    sys_prompt = _load_prompt("fee_extractor_text_v1")
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": text[:6000]},  # cap input
    ]
    try:
        rsp = llm.chat(
            tier="fast",
            messages=messages,
            max_tokens=600,
            temperature=0.0,
            response_format=build_response_format(),
        )
    except Exception as e:
        logger.exception("LLM text extract failed: %s", e)
        return ExtractionResult(extraction_method="llm_text",
                                 extraction_errors=[f"llm_error: {type(e).__name__}"])

    data = rsp.structured or {}
    return _result_from_dict(data, method="llm_text")


# --- Layer 4: LLM vision -----------------------------------------------------

def llm_vision_extract(pdf_path: str, llm: LLMClient, max_pages: int = 2) -> ExtractionResult:
    """Layer 4. Render PDF page(s) → image → vision LLM."""
    try:
        from pdf2image import convert_from_path  # type: ignore
    except ImportError:
        logger.warning("pdf2image not installed; vision layer skipped")
        return ExtractionResult(extraction_method="llm_vision",
                                 extraction_errors=["pdf2image_missing"])

    try:
        images = convert_from_path(pdf_path, dpi=150, last_page=max_pages)
    except Exception as e:
        return ExtractionResult(extraction_method="llm_vision",
                                 extraction_errors=[f"pdf2image_error: {e}"])
    if not images:
        return ExtractionResult(extraction_method="llm_vision",
                                 extraction_errors=["no_pages"])

    # Use only first page for cost control
    from io import BytesIO
    buf = BytesIO()
    images[0].save(buf, format="PNG")
    img_bytes = buf.getvalue()

    sys_prompt = _load_prompt("fee_extractor_vision_v1")
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": "Extract tour fees from this PDF page image."},
    ]
    try:
        rsp = llm.vision(
            messages=messages, image_bytes=img_bytes, max_tokens=600,
            response_format=build_response_format(),
        )
    except Exception as e:
        return ExtractionResult(extraction_method="llm_vision",
                                 extraction_errors=[f"llm_vision_error: {type(e).__name__}"])
    return _result_from_dict(rsp.structured or {}, method="llm_vision")


# --- Main ladder -------------------------------------------------------------

def extract_fees(pdf_path: str, llm: LLMClient, *,
                  skip_vision: bool = False) -> ExtractionResult:
    """
    Run the 4-layer ladder. Returns best result available.

    Decision flow:
      1) Layer 1: extract text. If text > 200 chars, attempt Layer 2/3.
      2) Layer 2: regex. If ≥3 required fields hit + confidence ≥ 0.7 → return.
      3) Layer 3: LLM on text. If confidence ≥ 0.8 → return.
      4) Layer 4: LLM vision (skipped if `skip_vision=True`).
    """
    if not os.path.exists(pdf_path):
        return ExtractionResult(extraction_method="none",
                                 extraction_errors=[f"pdf_not_found: {pdf_path}"])

    text = extract_text_from_pdf(pdf_path)

    if text and len(text) > 50:
        regex_result = regex_extract(text)
        required_hits = sum(1 for f in _REQUIRED_FIELDS if getattr(regex_result, f) is not None)
        if required_hits >= 3 and regex_result.extraction_confidence >= 0.7:
            return regex_result

        # Try LLM on text
        llm_result = llm_text_extract(text, llm)
        if llm_result.extraction_confidence >= 0.8:
            return llm_result

        # If LLM gave SOME data even at lower confidence, prefer it over empty regex
        if any(getattr(llm_result, f) is not None for f in _REQUIRED_FIELDS):
            best = llm_result
        else:
            best = regex_result
    else:
        best = ExtractionResult(extraction_method="none",
                                 extraction_errors=["insufficient_text"])

    if skip_vision or best.extraction_confidence >= 0.8:
        return best

    # Layer 4: vision fallback
    vision_result = llm_vision_extract(pdf_path, llm)
    if vision_result.extraction_confidence > best.extraction_confidence:
        return vision_result
    return best


# --- Helpers -----------------------------------------------------------------

def _load_prompt(name: str) -> str:
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "prompts")
    path = os.path.join(base, f"{name}.md")
    with open(path, "r", encoding="utf-8") as f:
        body = f.read()
    if body.startswith("---\n"):
        end = body.find("\n---\n", 4)
        if end != -1:
            body = body[end + 5:]
    return body


def _result_from_dict(data: dict, *, method: str) -> ExtractionResult:
    return ExtractionResult(
        tip_amount=data.get("tip_amount"),
        visa_fee=data.get("visa_fee"),
        visa_status=data.get("visa_status"),
        single_supplement=data.get("single_supplement"),
        infant_fee=data.get("infant_fee"),
        child_fee_no_bed=data.get("child_fee_no_bed"),
        deposit_amount=data.get("deposit_amount"),
        joinland_price=data.get("joinland_price"),
        mandatory_fees_summary=data.get("mandatory_fees_summary"),
        extraction_method=method,
        extraction_confidence=float(data.get("extraction_confidence", 0.0)),
        notes=data.get("notes", ""),
        source_page=data.get("source_page"),
        raw_snippet=data.get("raw_snippet"),
        tip_confidence=data.get("tip_confidence"),
        deposit_confidence=data.get("deposit_confidence"),
        single_supplement_confidence=data.get("single_supplement_confidence"),
        visa_confidence=data.get("visa_confidence"),
    )



# --- Page-level extraction (Sprint 3 R2) -------------------------------------

def extract_text_per_page(pdf_path: str) -> list[tuple[int, str]]:
    """
    Returns list of (page_no_1_indexed, text). Empty list if pdfplumber missing.
    """
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return []
    out: list[tuple[int, str]] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""
                out.append((i, text))
    except Exception as e:
        logger.warning("pdfplumber per-page failed on %s: %s", pdf_path, e)
    return out


def _merge_results(primary: ExtractionResult, other: ExtractionResult) -> ExtractionResult:
    """
    Merge two ExtractionResults.

    Phase 2 follow-up: for the money-critical fields (tip/deposit/single/visa),
    prefer the value with the HIGHER per-field confidence rather than always
    keeping primary's value. This is the difference between:
        - regex finds 19 for single_supplement (per-field conf 0.82) AND
        - vision finds 6000 for single_supplement (per-field conf 0.92)
    Old behavior kept the wrong 19; new behavior swaps in 6000 (higher conf).

    For non-money fields and metadata, the old "fill gaps" semantics are
    preserved (`primary` wins ties).
    """
    _CONF_FIELDS = (
        ("tip_amount", "tip_confidence"),
        ("deposit_amount", "deposit_confidence"),
        ("single_supplement", "single_supplement_confidence"),
    )
    for value_attr, conf_attr in _CONF_FIELDS:
        p_val = getattr(primary, value_attr)
        o_val = getattr(other, value_attr)
        p_conf = float(getattr(primary, conf_attr) or 0)
        o_conf = float(getattr(other, conf_attr) or 0)
        if p_val is None and o_val is not None:
            setattr(primary, value_attr, o_val)
            if o_conf > p_conf:
                setattr(primary, conf_attr, o_conf)
        elif p_val is not None and o_val is not None and o_conf > p_conf:
            setattr(primary, value_attr, o_val)
            setattr(primary, conf_attr, o_conf)

    # visa: prefer "exempt" status if either has it; otherwise fill-gap.
    if primary.visa_status is None and other.visa_status is not None:
        primary.visa_status = other.visa_status
    if primary.visa_fee is None and other.visa_fee is not None:
        primary.visa_fee = other.visa_fee
    if other.visa_confidence is not None and \
            float(other.visa_confidence) > float(primary.visa_confidence or 0):
        primary.visa_confidence = other.visa_confidence

    # Other fields: classic fill-gap semantics.
    for f in ("infant_fee", "child_fee_no_bed", "joinland_price",
              "mandatory_fees_summary"):
        if getattr(primary, f) is None and getattr(other, f) is not None:
            setattr(primary, f, getattr(other, f))

    if other.extraction_confidence > primary.extraction_confidence:
        primary.extraction_confidence = other.extraction_confidence
    if primary.source_page is None and other.source_page is not None:
        primary.source_page = other.source_page
    if not primary.raw_snippet and other.raw_snippet:
        primary.raw_snippet = other.raw_snippet
    return primary


def extract_fees_per_page(pdf_path: str, llm: LLMClient, *,
                            skip_vision: bool = False) -> ExtractionResult:
    """
    Per-page extraction per Sprint 3 R2 brief step 6:
        - regex on each page first
        - LLM (fast) only on pages with low confidence
        - Vision only on pages with no extractable text

    Returns merged result across all pages.
    """
    from .pdf_classifier_facade import classify_pdf  # late import for module locality (see facade below)

    pages = extract_text_per_page(pdf_path)
    if not pages:
        return ExtractionResult(extraction_method="none",
                                 extraction_errors=["no_pages_or_pdfplumber_missing"])

    cls = classify_pdf(pdf_path)
    merged = ExtractionResult(extraction_method="pdfplumber+regex",
                                source_page=None)

    # Step A: regex on every page
    for page_no, text in pages:
        if not text or len(text) < 30:
            continue
        page_r = regex_extract(text, source_page=page_no)
        merged = _merge_results(merged, page_r)

    # Step B: LLM-text on pages flagged "low confidence" (regex didn't fill required)
    if not merged.is_complete and not skip_vision:
        for page_no, text in pages:
            if not text or len(text) < 60:
                continue
            llm_r = llm_text_extract(text, llm)
            llm_r.source_page = page_no
            merged = _merge_results(merged, llm_r)
            if merged.is_complete:
                break

    # Step C: Vision fallback for scanned pages (only if still incomplete)
    if not merged.is_complete and not skip_vision and cls.needs_vision:
        vision_r = llm_vision_extract(pdf_path, llm, max_pages=2)
        if vision_r.extraction_confidence > 0:
            merged = _merge_results(merged, vision_r)
            merged.extraction_method = "llm_vision" if not merged.is_complete else merged.extraction_method

    return merged
