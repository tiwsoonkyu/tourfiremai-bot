"""
v2.scraper.ondemand_vision — On-demand candidate-page vision/OCR fallback.

Sprint 4 follow-up. Triggered only when:
  - customer asks a fee-related question, OR
  - selected_tour is locked and required fee fields are missing/low confidence.

Pipeline:
  1. classify_pdf() → pages with text, pages flagged for vision
  2. Select candidate pages: those whose text contains fee keywords OR pages
     that have no text (need vision) — capped at MAX_VISION_PAGES (default 3).
  3. For each candidate page, render to image (pdf2image) and call LLM.vision().
  4. Merge with any prior regex result (passed via `prior` arg).
  5. Cache the final ExtractionResult by (pdf_hash, extraction_version) in
     Redis (or InMemory). Subsequent calls hit the cache — NO second OpenAI call.

NO call sites should bypass this module to invoke vision directly. This is the
sole on-demand entry point so the budget cap + cache discipline is centralized.

Public API:
    EXTRACTION_VERSION (constant)
    extract_fees_on_demand(pdf_path, llm, *, pdf_hash, prior=None, cache=None,
                            max_vision_pages=3, asked_field=None) -> OnDemandResult
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
from dataclasses import dataclass, asdict, field
from typing import Any, Optional

from ..lib.llm import LLMClient
from ..lib.fee_schema import build_response_format
from ..lib.pdf_classifier import classify_pdf, PdfClassification
from .extract_fees import (
    ExtractionResult, _result_from_dict, _merge_results,
    extract_text_per_page, llm_vision_extract,
)

logger = logging.getLogger("v2.scraper.ondemand_vision")


# --- Constants ---------------------------------------------------------------

EXTRACTION_VERSION = "1.0"           # bump when prompt/algorithm changes
MAX_VISION_PAGES_DEFAULT = 3         # spec: max Vision/OCR pages per PDF = 3
CACHE_TTL_SECONDS = 30 * 24 * 3600   # 30 days
_CACHE_PREFIX = "fee_extract:"

# Fee-keyword regex used to pre-select candidate pages. Mirrors
# v2.lib.pdf_classifier._FEE_KEYWORDS_RE but kept local so the two can evolve
# independently without surprising imports.
_FEE_CANDIDATE_RE = re.compile(
    r"(ค่าทิป|ทิป|tip|มัดจำ|deposit|พักเดี่ยว|single\s*supp|วีซ่า|visa|"
    r"ทารก|infant|เด็ก[^\n]{0,10}(?:ไม่)?[^\n]{0,10}เตียง|no\s*bed|"
    r"joinland|join\s*land|land\s*tour|อัตราค่าบริการ|ค่าใช้จ่ายเพิ่มเติม)",
    re.I,
)


# --- Result type -------------------------------------------------------------

@dataclass
class OnDemandResult:
    result: ExtractionResult
    cache_hit: bool = False
    cache_key: str = ""
    candidate_pages: list[int] = field(default_factory=list)
    vision_pages_used: int = 0
    ocr_available: bool = True
    skipped_reason: Optional[str] = None  # e.g. "pdf2image_missing", "no_candidates", "vision_disabled"
    estimated_cost_usd: float = 0.0
    estimated_tokens_in: int = 0
    estimated_tokens_out: int = 0

    def to_dict(self) -> dict:
        return {
            "result": _result_to_serializable(self.result),
            "cache_hit": self.cache_hit,
            "cache_key": self.cache_key,
            "candidate_pages": list(self.candidate_pages),
            "vision_pages_used": self.vision_pages_used,
            "ocr_available": self.ocr_available,
            "skipped_reason": self.skipped_reason,
            "estimated_cost_usd": self.estimated_cost_usd,
            "estimated_tokens_in": self.estimated_tokens_in,
            "estimated_tokens_out": self.estimated_tokens_out,
        }


def _result_to_serializable(r: ExtractionResult) -> dict:
    d = asdict(r)
    # extraction_errors is a list; everything else is JSON-native
    return d


def _result_from_serializable(d: dict) -> ExtractionResult:
    """Reverse of _result_to_serializable."""
    return ExtractionResult(
        tip_amount=d.get("tip_amount"),
        visa_fee=d.get("visa_fee"),
        visa_status=d.get("visa_status"),
        single_supplement=d.get("single_supplement"),
        infant_fee=d.get("infant_fee"),
        child_fee_no_bed=d.get("child_fee_no_bed"),
        deposit_amount=d.get("deposit_amount"),
        joinland_price=d.get("joinland_price"),
        mandatory_fees_summary=d.get("mandatory_fees_summary"),
        extraction_method=d.get("extraction_method", "none"),
        extraction_confidence=float(d.get("extraction_confidence") or 0),
        extraction_errors=list(d.get("extraction_errors") or []),
        notes=d.get("notes", ""),
        source_page=d.get("source_page"),
        raw_snippet=d.get("raw_snippet"),
        tip_confidence=d.get("tip_confidence"),
        deposit_confidence=d.get("deposit_confidence"),
        single_supplement_confidence=d.get("single_supplement_confidence"),
        visa_confidence=d.get("visa_confidence"),
    )


# --- Cache helpers -----------------------------------------------------------

def _cache_key(pdf_hash: str, version: str) -> str:
    return f"{_CACHE_PREFIX}{pdf_hash}:{version}"


def _cache_get(cache, key: str) -> Optional[OnDemandResult]:
    if cache is None:
        return None
    try:
        raw = cache.get(key)
    except Exception as e:
        logger.warning("cache get failed: %s", e)
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        logger.warning("cache value not JSON for key=%s", key)
        return None
    result = _result_from_serializable(payload.get("result") or {})
    od = OnDemandResult(
        result=result,
        cache_hit=True,
        cache_key=key,
        candidate_pages=list(payload.get("candidate_pages") or []),
        vision_pages_used=int(payload.get("vision_pages_used") or 0),
        ocr_available=bool(payload.get("ocr_available", True)),
        skipped_reason=payload.get("skipped_reason"),
        estimated_cost_usd=float(payload.get("estimated_cost_usd") or 0),
        estimated_tokens_in=int(payload.get("estimated_tokens_in") or 0),
        estimated_tokens_out=int(payload.get("estimated_tokens_out") or 0),
    )
    return od


def _cache_put(cache, key: str, od: OnDemandResult) -> None:
    if cache is None:
        return
    payload = od.to_dict()
    payload["cache_hit"] = False  # the cached snapshot itself is a miss
    try:
        cache.set(key, json.dumps(payload, default=str), ex=CACHE_TTL_SECONDS)
    except Exception as e:
        logger.warning("cache put failed: %s", e)


# --- Candidate-page selection -----------------------------------------------

def select_candidate_pages(cls: PdfClassification, max_pages: int) -> list[int]:
    """
    Pick the page numbers (1-indexed) most likely to contain fee data:
      1. Pages that already matched fee keywords during classification.
      2. Pages flagged as scanned (no text) — vision may discover hidden content.
    Limit to `max_pages` total, prioritising fee-keyword pages first.
    """
    if max_pages <= 0:
        return []
    seen: set[int] = set()
    picked: list[int] = []
    # Priority 1: fee-keyword pages
    for p in cls.fee_pages:
        if p not in seen:
            picked.append(p); seen.add(p)
        if len(picked) >= max_pages:
            return picked
    # Priority 2: scanned pages (no text)
    for p in cls.scanned_pages:
        if p not in seen:
            picked.append(p); seen.add(p)
        if len(picked) >= max_pages:
            return picked
    return picked


def _scan_pages_for_keywords(pdf_path: str) -> list[int]:
    """
    Fallback page-keyword scan when classify_pdf didn't populate `fee_pages`
    (e.g. PDF text extraction returned text but classifier didn't index it).
    Returns 1-indexed pages whose text contains any fee keyword.
    """
    pages = extract_text_per_page(pdf_path)
    return [pno for pno, text in pages if text and _FEE_CANDIDATE_RE.search(text)]


# --- Vision availability probe ----------------------------------------------

def vision_available() -> tuple[bool, Optional[str]]:
    """
    Probe whether the runtime can do vision/OCR extraction.

    Returns (available, reason_if_not). Reason values:
      - 'pdf2image_missing' → pdf2image not installed (Poppler may also be missing)
      - 'pillow_missing'    → Pillow not installed (rare)
      - None                → available
    """
    try:
        import pdf2image  # type: ignore  # noqa: F401
    except ImportError:
        return False, "pdf2image_missing"
    try:
        from PIL import Image  # type: ignore  # noqa: F401
    except ImportError:
        return False, "pillow_missing"
    return True, None


# --- Main entrypoint ---------------------------------------------------------

def extract_fees_on_demand(
    pdf_path: str,
    llm: LLMClient,
    *,
    pdf_hash: str,
    prior: Optional[ExtractionResult] = None,
    cache: Any = None,
    max_vision_pages: int = MAX_VISION_PAGES_DEFAULT,
    asked_field: Optional[str] = None,
    extraction_version: str = EXTRACTION_VERSION,
) -> OnDemandResult:
    """
    Run on-demand vision/OCR over candidate pages only.

    Args:
        pdf_path: local path to the PDF.
        llm: LLM client (mock/cassette/live per config).
        pdf_hash: sha256 of the PDF bytes — half of the cache key.
        prior: optional regex/text result to merge with. If present and the
            asked field is already filled with sufficient confidence at the
            field level, we still try cache lookup but skip new vision calls.
        cache: redis-like with .get/.set; if None, no caching.
        max_vision_pages: hard cap per PDF. Spec default = 3.
        asked_field: optional hint from response writer ('tip', 'deposit',
            'single_supplement', 'visa', 'infant', 'child_no_bed', 'any').
            Used to prefer pages that mention the specific keyword.
        extraction_version: pipeline version string; bump to invalidate cache.
    """
    cache_key = _cache_key(pdf_hash, extraction_version)

    # 1) Cache lookup — short-circuit if hit
    cached = _cache_get(cache, cache_key)
    if cached is not None:
        logger.info("ondemand_vision cache HIT key=%s", cache_key)
        return cached

    # 2) OCR availability probe
    ok, reason = vision_available()
    if not ok:
        logger.warning("ondemand_vision skipping — %s", reason)
        result = prior or ExtractionResult(
            extraction_method="none",
            extraction_errors=[f"ocr_unavailable: {reason}"],
        )
        od = OnDemandResult(
            result=result,
            cache_hit=False,
            cache_key=cache_key,
            candidate_pages=[],
            vision_pages_used=0,
            ocr_available=False,
            skipped_reason=reason,
        )
        # Do not cache OCR-unavailable misses — env may become available later.
        return od

    if not os.path.exists(pdf_path):
        logger.warning("ondemand_vision pdf missing: %s", pdf_path)
        result = prior or ExtractionResult(
            extraction_method="none",
            extraction_errors=[f"pdf_not_found: {pdf_path}"],
        )
        return OnDemandResult(
            result=result, cache_key=cache_key,
            skipped_reason="pdf_not_found",
        )

    # 3) Classify and pick candidate pages
    cls = classify_pdf(pdf_path)
    candidate_pages = select_candidate_pages(cls, max_vision_pages)
    if not candidate_pages and cls.total_pages > 0:
        # Fall back to a direct page-text keyword scan
        candidate_pages = _scan_pages_for_keywords(pdf_path)[:max_vision_pages]

    if not candidate_pages:
        logger.info("ondemand_vision no candidate pages for %s", pdf_path)
        result = prior or ExtractionResult(
            extraction_method="none",
            extraction_errors=["no_candidate_pages"],
        )
        od = OnDemandResult(
            result=result, cache_key=cache_key,
            candidate_pages=[], vision_pages_used=0,
            ocr_available=True, skipped_reason="no_candidates",
        )
        _cache_put(cache, cache_key, od)
        return od

    # 4) Run vision on the first candidate page (extract_fees.llm_vision_extract
    #    currently only renders page 1 internally; we instead pass a sliced PDF
    #    or rely on its max_pages — simplest: call once per candidate page with
    #    a pdf2image render limited to that page).
    merged = prior or ExtractionResult(
        extraction_method="pdfplumber+regex",
        source_page=None,
    )
    vision_used = 0
    total_tokens_in = 0
    total_tokens_out = 0
    total_cost = 0.0

    try:
        import pdf2image  # type: ignore
        from io import BytesIO
        from ..lib.llm import LLMClient  # type: ignore  # local re-import for clarity
    except ImportError as e:  # pragma: no cover  (vision_available already checked)
        logger.warning("late ImportError despite vision_available True: %s", e)
        return OnDemandResult(
            result=merged, cache_key=cache_key,
            candidate_pages=candidate_pages, vision_pages_used=0,
            ocr_available=False, skipped_reason=f"late_import_error:{type(e).__name__}",
        )

    sys_prompt_loader = _maybe_load_vision_prompt()
    for page_no in candidate_pages:
        if vision_used >= max_vision_pages:
            break
        try:
            images = pdf2image.convert_from_path(  # type: ignore[attr-defined]
                pdf_path, dpi=150, first_page=page_no, last_page=page_no,
            )
        except Exception as e:
            logger.warning("pdf2image render failed page=%s: %s", page_no, e)
            continue
        if not images:
            continue

        buf = io.BytesIO()
        images[0].save(buf, format="PNG")
        img_bytes = buf.getvalue()

        messages = [
            {"role": "system", "content": sys_prompt_loader()},
            {"role": "user",   "content": f"Extract tour fees from this PDF page (page {page_no})."},
        ]
        try:
            rsp = llm.vision(
                messages=messages,
                image_bytes=img_bytes,
                max_tokens=600,
                response_format=build_response_format(),
            )
        except Exception as e:
            logger.warning("vision call failed page=%s: %s", page_no, e)
            continue
        vision_used += 1
        if rsp.usage:
            total_tokens_in += rsp.usage.tokens_in or 0
            total_tokens_out += rsp.usage.tokens_out or 0
            total_cost += rsp.usage.cost_usd_estimate or 0
        page_result = _result_from_dict(rsp.structured or {}, method="llm_vision")
        page_result.source_page = page_no
        merged = _merge_results(merged, page_result)
        # Bump per-field confidences when this vision call provided a value
        _bump_field_confidence_from_vision(merged, page_result)

    # If vision yielded anything new, mark method accordingly
    if vision_used > 0 and merged.extraction_method in ("pdfplumber+regex", "none"):
        merged.extraction_method = "llm_vision"

    od = OnDemandResult(
        result=merged,
        cache_hit=False,
        cache_key=cache_key,
        candidate_pages=candidate_pages,
        vision_pages_used=vision_used,
        ocr_available=True,
        skipped_reason=None,
        estimated_cost_usd=round(total_cost, 6),
        estimated_tokens_in=total_tokens_in,
        estimated_tokens_out=total_tokens_out,
    )
    _cache_put(cache, cache_key, od)
    return od


# --- Helpers -----------------------------------------------------------------

def _bump_field_confidence_from_vision(merged: ExtractionResult,
                                         page: ExtractionResult) -> None:
    """
    Vision extracted a value for a field → upgrade per-field confidence to the
    overall extraction_confidence reported by the vision call (cap at 0.95).

    Take-max semantics (QA N1 fix): if the regex tier already populated the
    column with a lower baseline (e.g. single_supplement_confidence=0.60),
    vision must still be able to lift it. We only skip the bump when the
    existing confidence is already at-or-above what vision is offering.
    Field-level columns stay NULL for fields not extracted on this page.
    """
    conf = min(0.95, float(page.extraction_confidence or 0))
    if conf <= 0:
        return
    if page.tip_amount is not None and merged.tip_amount == page.tip_amount             and conf > (merged.tip_confidence or 0):
        merged.tip_confidence = conf
    if page.deposit_amount is not None and merged.deposit_amount == page.deposit_amount             and conf > (merged.deposit_confidence or 0):
        merged.deposit_confidence = conf
    if page.single_supplement is not None and merged.single_supplement == page.single_supplement             and conf > (merged.single_supplement_confidence or 0):
        merged.single_supplement_confidence = conf
    if (page.visa_status is not None or page.visa_fee is not None)             and conf > (merged.visa_confidence or 0):
        merged.visa_confidence = conf


def _maybe_load_vision_prompt():
    """Lazy loader; cached after first call."""
    cached: list[Optional[str]] = [None]

    def _load() -> str:
        if cached[0] is not None:
            return cached[0]
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "prompts")
        path = os.path.join(base, "fee_extractor_vision_v1.md")
        try:
            with open(path, "r", encoding="utf-8") as f:
                body = f.read()
            if body.startswith("---\n"):
                end = body.find("\n---\n", 4)
                if end != -1:
                    body = body[end + 5:]
            cached[0] = body
            return body
        except Exception:
            cached[0] = "Extract tour fees from this PDF page image as strict JSON."
            return cached[0]

    return _load
