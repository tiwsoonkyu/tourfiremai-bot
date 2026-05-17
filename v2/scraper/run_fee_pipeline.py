"""
v2.scraper.run_fee_pipeline — Offline driver for the full PDF fee pipeline.

Runs the 10-step process for one or many tours:
  1. find PDF URL (discover_pdf_url)
  2. download PDF (download_pdf)
  3. calculate pdf_hash (sha256)
  4. detect text/scanned/mixed (classify_pdf)
  5. extract text (pdfplumber per-page)
  6. OCR/Vision fallback only for low-text/no-keyword pages
  7. parse fees with regex first
  8. LLM only for ambiguous extraction
  9. save to tour_fees
 10. handoff signal if fee missing (returned to caller)

CLI usage:
    python -m v2.scraper.run_fee_pipeline --tour-id <uuid>
    python -m v2.scraper.run_fee_pipeline --web-code ap242455
    python -m v2.scraper.run_fee_pipeline --all                # batch
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from typing import Optional

from ..lib import config as cfg_module
from ..lib.cache import make_redis  # noqa
from ..lib.db import make_supabase_from_config
from ..lib.llm import make_llm_client, MockLLMClient
from ..lib.pdf_classifier import classify_pdf
from .discover_pdf_url import discover_pdf_url
from .download_pdf import download_pdf
from .extract_fees import extract_fees_per_page, ExtractionResult
from .save_fees import upsert_tour_fees

logger = logging.getLogger("v2.scraper.pipeline")


@dataclass
class PipelineResult:
    tour_id: str
    web_code: str
    pdf_url: Optional[str]
    pdf_hash: Optional[str]
    pdf_kind: str = "unknown"
    extraction: Optional[ExtractionResult] = None
    saved: bool = False
    needs_handoff: bool = False
    handoff_reason: Optional[str] = None
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


def run_for_tour(
    *,
    tour_row: dict,
    supabase,
    llm,
    cache_dir: str = "/tmp/v2-pdf-cache",
    skip_vision: bool = False,
) -> PipelineResult:
    """Run the 10-step pipeline for one tour row from tours_canonical."""
    web_code = tour_row["web_code"]
    tour_id = tour_row["id"]
    tour_code_real = tour_row.get("tour_code_real")

    result = PipelineResult(tour_id=tour_id, web_code=web_code, pdf_url=None, pdf_hash=None)

    # Step 1: discover URL
    disc = discover_pdf_url(web_code, supabase=supabase, prefer_db=True)
    if not disc.pdf_url:
        result.errors.append(f"discover_failed: {disc.notes}")
        result.needs_handoff = True
        result.handoff_reason = "pdf_url_not_found"
        return result
    result.pdf_url = disc.pdf_url

    # Step 2 + 3: download + hash
    try:
        artifact = download_pdf(disc.pdf_url, cache_dir=cache_dir)
    except Exception as e:
        result.errors.append(f"download_failed: {type(e).__name__}: {e}")
        result.needs_handoff = True
        result.handoff_reason = "pdf_download_failed"
        return result
    result.pdf_hash = artifact.sha256

    # Step 4: classify
    cls = classify_pdf(artifact.local_path)
    result.pdf_kind = cls.kind

    # Step 5 + 6 + 7 + 8: layered per-page extraction
    extraction = extract_fees_per_page(artifact.local_path, llm, skip_vision=skip_vision)
    result.extraction = extraction

    # Step 9: save
    try:
        upsert_tour_fees(
            supabase,
            tour_id=tour_id, tour_code_real=tour_code_real,
            pdf_url=disc.pdf_url, pdf_hash=artifact.sha256,
            result=extraction,
        )
        result.saved = True
    except Exception as e:
        result.errors.append(f"save_failed: {type(e).__name__}: {e}")

    # Step 10: handoff signal
    if not extraction.is_complete or extraction.extraction_confidence < 0.7:
        result.needs_handoff = True
        result.handoff_reason = (
            f"fee_incomplete (confidence={extraction.extraction_confidence:.2f}, "
            f"missing={_missing_required(extraction)})"
        )

    return result


def _missing_required(r: ExtractionResult) -> list[str]:
    missing = []
    if r.tip_amount is None: missing.append("tip_amount")
    if r.single_supplement is None: missing.append("single_supplement")
    if r.deposit_amount is None: missing.append("deposit_amount")
    if r.visa_fee is None and r.visa_status is None: missing.append("visa")
    return missing


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run V2 fee pipeline offline")
    parser.add_argument("--tour-id", help="Single tour UUID")
    parser.add_argument("--web-code", help="Single tour web_code")
    parser.add_argument("--all", action="store_true", help="All active tours")
    parser.add_argument("--limit", type=int, default=10, help="Max tours when --all")
    parser.add_argument("--skip-vision", action="store_true")
    parser.add_argument("--mock-llm", action="store_true", help="Force mock LLM (default if no API key)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                         format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

    config = cfg_module.load_config(strict=True)

    # LLM: mock by default; live only if explicitly NOT --mock-llm AND key exists
    if args.mock_llm or not config.openai_api_key:
        llm = MockLLMClient(config)
        logger.info("Using MockLLMClient (no live API calls)")
    else:
        llm = make_llm_client(config)

    supabase = make_supabase_from_config(config)

    if args.tour_id:
        row = supabase.table("tours_canonical").select_one({"id": args.tour_id})
        if not row:
            logger.error("tour_id %s not found", args.tour_id)
            return 2
        rows = [row]
    elif args.web_code:
        row = supabase.table("tours_canonical").select_one({"web_code": args.web_code})
        if not row:
            logger.error("web_code %s not found", args.web_code)
            return 2
        rows = [row]
    elif args.all:
        # Use raw cursor — DB adapter doesn't expose limit/order natively here
        with supabase.table("tours_canonical")._cursor() as cur:
            cur.execute(
                'SELECT id, web_code, tour_code_real, name, pdf_url '
                'FROM "tours_canonical" WHERE is_active = TRUE ORDER BY last_synced_at DESC LIMIT %s',
                [args.limit],
            )
            rows = []
            cols = [d.name for d in cur.description]
            for r in cur.fetchall():
                rows.append(dict(zip(cols, r)))
    else:
        parser.error("specify --tour-id, --web-code, or --all")

    total = len(rows)
    saved = 0
    handoff = 0
    errors = 0
    for i, row in enumerate(rows, 1):
        logger.info("[%d/%d] %s (%s)", i, total, row["web_code"], row.get("name", "")[:40])
        try:
            res = run_for_tour(tour_row=row, supabase=supabase, llm=llm,
                                skip_vision=args.skip_vision)
            if res.saved: saved += 1
            if res.needs_handoff: handoff += 1
            if res.errors: errors += len(res.errors)
            logger.info("  → kind=%s conf=%.2f handoff=%s errors=%s",
                         res.pdf_kind,
                         res.extraction.extraction_confidence if res.extraction else 0,
                         res.needs_handoff, res.errors)
        except Exception as e:
            logger.exception("[%d/%d] unhandled error: %s", i, total, e)
            errors += 1

    logger.info("=" * 60)
    logger.info("Pipeline summary: total=%d saved=%d handoff=%d errors=%d",
                 total, saved, handoff, errors)
    return 0


if __name__ == "__main__":
    sys.exit(main())
