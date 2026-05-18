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
    parser.add_argument("--pdf-corpus", action="store_true",
                         help="Run on v2/tests/fixtures/pdfs/* corpus instead of DB (per-page Sprint 3 path)")
    parser.add_argument("--pdf-corpus-ondemand", action="store_true",
                         help=("Run corpus via the Sprint 4 follow-up on-demand "
                                "runtime path: per (PDF, asked_field) call "
                                "extract_fees_on_demand with cache + page cap. "
                                "Use this for Phase 2 live recording — it "
                                "measures the same code path the bot will run."))
    parser.add_argument("--limit", type=int, default=10, help="Max tours when --all")
    parser.add_argument("--skip-vision", action="store_true")
    parser.add_argument("--mock-llm", action="store_true", help="Force mock LLM (default if no API key)")
    parser.add_argument("--record", action="store_true",
                         help="Record cassettes (requires live OPENAI_API_KEY)")
    parser.add_argument("--accuracy", action="store_true",
                         help="Run accuracy report against ground_truth/ fixtures")
    parser.add_argument("--cassette-dir", default=None)
    parser.add_argument("--benchmark-providers", default=None,
                         help=("Comma-separated provider names to benchmark "
                                "against the PDF corpus + ground-truth fixtures. "
                                "Default \"mock\" needs no credentials. Paid "
                                "providers (mistral_ocr, google_document_ai, "
                                "aws_textract) are skipped at runtime if their "
                                "credentials/SDK are missing — no network call."))
    parser.add_argument("--replay-cassette", action="store_true",
                         help=("Force cassette replay mode (no network). Equivalent to setting V2_STAGING_OPENAI_TEST_MODE=cassette before running. Reads cassettes from --cassette-dir or v2/tests/cassettes/."))
    parser.add_argument("--output-report", default=None,
                         help="Path to save accuracy report markdown")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                         format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

    config = cfg_module.load_config(strict=True)

    # LLM selection — explicit flags take precedence; otherwise config.openai_test_mode wins.
    # The 4 modes (mock | cassette | record | live) are honored end-to-end so a
    # caller can verify the cassettes recorded by Phase 2 without network access.
    explicit_mode = None
    if args.mock_llm:
        explicit_mode = "mock"
    elif args.replay_cassette:
        explicit_mode = "cassette"
    elif args.record:
        explicit_mode = "record"

    effective_mode = (explicit_mode or
                       (config.openai_test_mode or "mock").lower())

    if effective_mode == "record":
        if not config.openai_api_key:
            logger.error("record mode requires V2_STAGING_OPENAI_API_KEY")
            return 2
        try:
            object.__setattr__(config, "openai_test_mode", "record")
        except Exception:
            pass
        os.environ["V2_STAGING_OPENAI_TEST_MODE"] = "record"
        llm = make_llm_client(config, cassette_dir=args.cassette_dir)
        logger.info("Using RecordingLLMClient (live API + cassette persistence)")
    elif effective_mode == "cassette":
        try:
            object.__setattr__(config, "openai_test_mode", "cassette")
        except Exception:
            pass
        os.environ["V2_STAGING_OPENAI_TEST_MODE"] = "cassette"
        llm = make_llm_client(config, cassette_dir=args.cassette_dir)
        logger.info("Using CassetteLLMClient (replay, NO network)")
    elif effective_mode == "live":
        if not config.openai_api_key:
            logger.error("live mode requires V2_STAGING_OPENAI_API_KEY")
            return 2
        llm = make_llm_client(config)
        logger.info("Using OpenAILLMClient (live, no recording)")
    else:  # mock (default)
        llm = MockLLMClient(config)
        logger.info("Using MockLLMClient (mode=%s)", effective_mode)

    # QA L2 fix: skip Supabase config/client init for the corpus-only / benchmark-only paths.
    # Those paths read fixture PDFs + ground-truth JSON from disk; they do not
    # touch tour_fees or any other DB table. Initializing Supabase here would
    # require V2_STAGING_DB_* env vars even when the operator only wants a
    # mock benchmark run.
    needs_supabase = not (args.pdf_corpus or args.pdf_corpus_ondemand or args.benchmark_providers)
    supabase = make_supabase_from_config(config) if needs_supabase else None

    if args.benchmark_providers:
        return _run_benchmark_providers(args)
    if args.pdf_corpus_ondemand:
        return _run_pdf_corpus_ondemand(args, config, llm)
    if args.pdf_corpus:
        return _run_pdf_corpus(args, config, llm, supabase)

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



def _run_pdf_corpus(args, config, llm, supabase) -> int:
    """
    Corpus mode: iterate v2/tests/fixtures/pdfs/{text_based,scanned,mixed}/*.pdf
    extract fees, optionally accuracy-grade against fixtures/ground_truth/.

    Does NOT write to tour_fees (corpus is a fixtures dir, not production tours).
    """
    import glob
    from .extract_fees import extract_fees_per_page
    from .extraction_accuracy import (
        run_accuracy_corpus, format_report_markdown,
    )

    fixture_root = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "tests", "fixtures",
    )
    pdf_dirs = [
        os.path.join(fixture_root, "pdfs", sub)
        for sub in ("text_based", "scanned", "mixed")
    ]
    pdfs: list[str] = []
    for d in pdf_dirs:
        pdfs.extend(sorted(glob.glob(os.path.join(d, "*.pdf"))))

    if not pdfs:
        logger.warning("No PDFs found in %s — drop fixtures and re-run", pdf_dirs)
        return 1

    logger.info("Processing %d PDFs", len(pdfs))
    results: list[tuple[str, "ExtractionResult"]] = []
    for pdf_path in pdfs:
        basename = os.path.basename(pdf_path)
        logger.info("→ %s", basename)
        try:
            res = extract_fees_per_page(pdf_path, llm, skip_vision=args.skip_vision)
            logger.info("   conf=%.2f method=%s tip=%s deposit=%s",
                         res.extraction_confidence, res.extraction_method,
                         res.tip_amount, res.deposit_amount)
            results.append((basename, res))
        except Exception as e:
            logger.exception("   FAILED: %s", e)

    if args.accuracy:
        gt_dir = os.path.join(fixture_root, "ground_truth")
        report = run_accuracy_corpus(results, gt_dir)
        md = format_report_markdown(report)
        if args.output_report:
            with open(args.output_report, "w", encoding="utf-8") as f:
                f.write(md)
            logger.info("Accuracy report → %s", args.output_report)
        else:
            print(md)
        logger.info("Avg overall=%.1f%% hardest=%.1f%%",
                     report.avg_overall * 100, report.avg_hardest_required * 100)
    else:
        for basename, res in results:
            print(f"{basename}: conf={res.extraction_confidence:.2f}, method={res.extraction_method}")

    return 0

def _run_pdf_corpus_ondemand(args, config, llm) -> int:
    """
    Corpus mode (Sprint 4 follow-up): drive `extract_fees_on_demand` per (PDF,
    asked_field) so the recording measures the EXACT code path the bot will
    run when a customer asks a fee question on a locked tour.

    For each fixture PDF:
      1. Compute pdf_hash from the file bytes (matches download_pdf semantics).
      2. Run regex once to seed a `prior` ExtractionResult (mirrors what the
         orchestrator passes in from the DB row).
      3. For each `asked_field` in (tip, deposit, single_supplement, visa),
         call `extract_fees_on_demand(... asked_field=...)`.
         - First call per PDF: cache miss, vision runs on up to 3 candidate
           pages (per spec).
         - Subsequent calls per same PDF: cache HIT, no second OpenAI call.
      4. Optionally write the final merged result to
         `docs/SPRINT_4_ACCURACY_REPORT.md` via the existing accuracy framework
         (--accuracy flag).

    Does NOT touch the live DB. Does NOT call upsert_tour_fees (corpus is for
    measurement; production upsert happens through the orchestrator runtime
    wire-in for real customer tours).
    """
    import glob
    import hashlib
    from .extract_fees import (
        extract_fees_per_page, regex_extract, ExtractionResult,
        extract_text_from_pdf,
    )
    from .ondemand_vision import extract_fees_on_demand, EXTRACTION_VERSION
    from ..lib.cache import _InMemoryRedis
    from .extraction_accuracy import run_accuracy_corpus, format_report_markdown

    fixture_root = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "tests", "fixtures",
    )
    pdf_dirs = [
        os.path.join(fixture_root, "pdfs", sub)
        for sub in ("text_based", "scanned", "mixed")
    ]
    pdfs: list[str] = []
    for d in pdf_dirs:
        pdfs.extend(sorted(glob.glob(os.path.join(d, "*.pdf"))))
    if not pdfs:
        logger.warning("No PDFs found in %s — drop fixtures and re-run", pdf_dirs)
        return 1

    ASKED_FIELDS = ("tip", "deposit", "single_supplement", "visa")
    logger.info("On-demand corpus mode: %d PDFs × %d asked_fields = %d runs",
                 len(pdfs), len(ASKED_FIELDS), len(pdfs) * len(ASKED_FIELDS))

    # Shared in-memory cache so the (pdf_hash, version) cache hit assertion
    # across asked_fields holds. In production the orchestrator uses Redis.
    cache = _InMemoryRedis()

    results: list[tuple[str, ExtractionResult]] = []
    cache_hits = 0
    cache_misses = 0
    total_tokens_in = 0
    total_tokens_out = 0
    total_cost = 0.0
    unpriced_calls = 0

    for pdf_path in pdfs:
        basename = os.path.basename(pdf_path)
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()

        # Step 1: regex-only baseline (acts as `prior` for vision lift)
        text = extract_text_from_pdf(pdf_path)
        prior = regex_extract(text) if text else ExtractionResult(extraction_method="none")

        merged: ExtractionResult = prior
        for asked_field in ASKED_FIELDS:
            od = extract_fees_on_demand(
                pdf_path, llm,
                pdf_hash=pdf_hash,
                prior=merged,
                cache=cache,
                max_vision_pages=3,
                asked_field=asked_field,
                extraction_version=EXTRACTION_VERSION,
            )
            if od.cache_hit:
                cache_hits += 1
            else:
                cache_misses += 1
            total_tokens_in += od.estimated_tokens_in
            total_tokens_out += od.estimated_tokens_out
            total_cost += od.estimated_cost_usd
            if not od.cache_hit and od.vision_pages_used > 0 \
                    and od.estimated_cost_usd <= 0 \
                    and (od.estimated_tokens_in + od.estimated_tokens_out) > 0:
                unpriced_calls += 1
            merged = od.result  # carry confidence + values forward
            logger.info("   %s [%s] cache_hit=%s vision_pages=%d ocr_avail=%s",
                         basename, asked_field, od.cache_hit,
                         od.vision_pages_used, od.ocr_available)
        results.append((basename, merged))

    logger.info("=" * 60)
    logger.info("On-demand corpus summary: cache_hits=%d cache_misses=%d",
                 cache_hits, cache_misses)
    logger.info("Aggregate tokens_in=%d tokens_out=%d cost_usd=%.4f",
                 total_tokens_in, total_tokens_out, total_cost)
    if total_cost > 5.0:
        logger.error("BUDGET CAP EXCEEDED ($5.00) — recorded cost $%.4f", total_cost)

    if args.accuracy:
        gt_dir = os.path.join(fixture_root, "ground_truth")
        report = run_accuracy_corpus(results, gt_dir)
        md = format_report_markdown(report)
        # Append run summary to the report.
        # Cost is "known" only when every vision call had a priced model; if
        # some used a model not in v2/lib/llm_pricing, surface as lower bound.
        cost_label = (
            f"${total_cost:.4f}" if total_cost > 0
            else (f"unknown — {unpriced_calls} call(s) used a model not in "
                   f"v2/lib/llm_pricing.MODEL_PRICING_USD_PER_TOKEN")
            if unpriced_calls > 0
            else "$0.0000 (no LLM calls — all hits served from cache)"
        )
        if unpriced_calls > 0 and total_cost > 0:
            cost_label = (
                f"≥ ${total_cost:.4f} (lower bound — {unpriced_calls} call(s) "
                f"used a model not in pricing table)"
            )
        md += (
            f"\n\n## On-demand runtime stats\n"
            f"- Cache hits: {cache_hits}\n"
            f"- Cache misses: {cache_misses}\n"
            f"- Total tokens in: {total_tokens_in}\n"
            f"- Total tokens out: {total_tokens_out}\n"
            f"- Total cost (USD est.): {cost_label}\n"
            f"- Budget cap: $5.00 ({'OK' if (total_cost or 0) <= 5.0 else 'EXCEEDED'})\n"
        )
        if args.output_report:
            with open(args.output_report, "w", encoding="utf-8") as f:
                f.write(md)
            logger.info("Accuracy report → %s", args.output_report)
        else:
            print(md)
        logger.info("Avg overall=%.1f%% hardest=%.1f%%",
                     report.avg_overall * 100, report.avg_hardest_required * 100)
    else:
        for basename, res in results:
            print(f"{basename}: conf={res.extraction_confidence:.2f} "
                   f"method={res.extraction_method} tip={res.tip_amount} "
                   f"single={res.single_supplement}")

    return 0


def _run_benchmark_providers(args) -> int:
    """Run provider-comparison benchmark over the fixture PDF corpus.

    Pure-process default: only the `mock` provider is benchmarked. Pass
    `--benchmark-providers mock,mistral_ocr` to ALSO probe paid providers —
    paid providers whose creds/SDK are missing are simply skipped (no
    network call, no exception).
    """
    import glob
    from .benchmark_providers import benchmark_providers, format_benchmark_markdown

    fixture_root = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "tests", "fixtures",
    )
    pdf_dirs = [os.path.join(fixture_root, "pdfs", sub)
                 for sub in ("text_based", "scanned", "mixed")]
    pdfs: list[str] = []
    for d in pdf_dirs:
        pdfs.extend(sorted(glob.glob(os.path.join(d, "*.pdf"))))
    if not pdfs:
        logger.warning("benchmark: no PDFs in %s", pdf_dirs)
        return 1

    provider_names = [p.strip() for p in args.benchmark_providers.split(",") if p.strip()]
    if not provider_names:
        provider_names = ["mock"]
    logger.info("benchmark: %d PDFs × providers=%s", len(pdfs), provider_names)

    gt_dir = os.path.join(fixture_root, "ground_truth")
    report = benchmark_providers(pdfs, provider_names, ground_truth_dir=gt_dir)
    md = format_benchmark_markdown(report)
    if args.output_report:
        with open(args.output_report, "w", encoding="utf-8") as f:
            f.write(md)
        logger.info("benchmark report → %s", args.output_report)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
