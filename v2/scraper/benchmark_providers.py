"""
v2.scraper.benchmark_providers — Compare regex+vision against optional
document-parser providers on the same PDF corpus.

Sprint 4 Phase 2 live-accuracy follow-up. Lets us measure whether a paid
OCR layer would improve fee accuracy WITHOUT making any live paid call: the
default `--providers mock` path runs entirely in-process. Paid providers are
skipped (with a clear log line) when their credentials/SDK are missing —
they never trigger a network call.

CLI surface lives in run_fee_pipeline.py via `--benchmark-providers`. This
module is the pure function the CLI delegates to, so tests can call it
directly without going through argparse.

Public API:
    BenchmarkPerProvider             — per-provider summary
    BenchmarkReport                  — aggregate over providers + corpus
    benchmark_providers(pdfs, providers, *, ground_truth_dir) -> BenchmarkReport
    format_benchmark_markdown(report) -> str
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from .document_parser_provider import (
    DocumentParseResult, ProviderNotAvailableError, ProviderNotImplementedError,
    make_document_parser, available_providers,
)
from .extract_fees import ExtractionResult

logger = logging.getLogger("v2.scraper.benchmark_providers")


# ---- Result types ----------------------------------------------------------

@dataclass
class BenchmarkPerPdf:
    pdf_name: str
    provider: str
    overall: float
    hardest_required: float
    per_field: dict[str, float]
    source_page_match: Optional[bool]
    latency_ms: int
    estimated_cost_usd: Optional[float]
    error: Optional[str] = None


@dataclass
class BenchmarkPerProvider:
    provider: str
    available: bool
    skip_reason: Optional[str]
    per_pdf: list[BenchmarkPerPdf] = field(default_factory=list)
    avg_overall: float = 0.0
    avg_hardest: float = 0.0
    total_cost_usd: Optional[float] = None
    total_latency_ms: int = 0
    unpriced_calls: int = 0


@dataclass
class BenchmarkReport:
    pdfs: list[str]
    providers: list[BenchmarkPerProvider]
    notes: list[str] = field(default_factory=list)


# ---- Grading bridge ---------------------------------------------------------

def _parse_result_to_extraction(r: DocumentParseResult) -> ExtractionResult:
    """Convert provider output → ExtractionResult so the existing
    extraction_accuracy grader can score it."""
    ff = r.fee_fields or {}
    return ExtractionResult(
        tip_amount=ff.get("tip_amount"),
        deposit_amount=ff.get("deposit_amount"),
        single_supplement=ff.get("single_supplement"),
        visa_fee=ff.get("visa_fee"),
        visa_status=r.visa_status,
        infant_fee=ff.get("infant_fee"),
        child_fee_no_bed=ff.get("child_fee_no_bed"),
        joinland_price=ff.get("joinland_price"),
        extraction_method=f"provider:{r.provider}",
        extraction_confidence=max(
            (v for v in (r.fee_field_confidences or {}).values() if v is not None),
            default=0.0,
        ),
        source_page=r.source_page,
        raw_snippet=r.raw_snippet,
        tip_confidence=(r.fee_field_confidences or {}).get("tip_confidence"),
        deposit_confidence=(r.fee_field_confidences or {}).get("deposit_confidence"),
        single_supplement_confidence=(r.fee_field_confidences or {}).get("single_supplement_confidence"),
        visa_confidence=(r.fee_field_confidences or {}).get("visa_confidence"),
    )


# ---- Main entry point ------------------------------------------------------

def benchmark_providers(
    pdfs: list[str],
    providers: list[str],
    *,
    ground_truth_dir: str,
) -> BenchmarkReport:
    """Run each provider's `parse()` on each PDF, grade against ground truth,
    return a structured report.

    Hard rules enforced here:
      - Providers whose `is_available()` is False are SKIPPED with a clear
        `skip_reason`. No `parse()` is called, no exception thrown, no
        network touched.
      - Any `ProviderNotImplementedError` (stub providers) is caught and
        recorded as an `error` on the per-pdf row; benchmark continues.
      - The function does NOT fall back from one provider to another; each
        provider is benchmarked independently.
    """
    from .extraction_accuracy import grade_extraction

    report = BenchmarkReport(pdfs=list(pdfs), providers=[])

    for provider_name in providers:
        try:
            parser = make_document_parser(provider_name)
        except ValueError as e:
            report.notes.append(f"{provider_name}: {e}")
            report.providers.append(BenchmarkPerProvider(
                provider=provider_name, available=False,
                skip_reason=f"unknown_provider:{provider_name}",
            ))
            continue

        ok, reason = parser.is_available()
        per_provider = BenchmarkPerProvider(
            provider=provider_name, available=ok, skip_reason=reason if not ok else None,
        )
        if not ok:
            logger.info("benchmark: skipping %s (%s)", provider_name, reason)
            report.providers.append(per_provider)
            continue

        # Run every PDF.
        overall_sum = 0.0
        hardest_sum = 0.0
        cost_sum = 0.0
        cost_unknown_count = 0
        for pdf_path in pdfs:
            row = _benchmark_one(pdf_path, parser, ground_truth_dir, grade_extraction)
            per_provider.per_pdf.append(row)
            if row.error is not None:
                continue
            overall_sum += row.overall
            hardest_sum += row.hardest_required
            per_provider.total_latency_ms += row.latency_ms
            if row.estimated_cost_usd is None:
                cost_unknown_count += 1
            else:
                cost_sum += row.estimated_cost_usd

        valid = [r for r in per_provider.per_pdf if r.error is None]
        if valid:
            per_provider.avg_overall = round(overall_sum / len(valid), 4)
            per_provider.avg_hardest = round(hardest_sum / len(valid), 4)
        per_provider.unpriced_calls = cost_unknown_count
        per_provider.total_cost_usd = (
            round(cost_sum, 6) if cost_unknown_count == 0
            else (round(cost_sum, 6) if cost_sum > 0 else None)
        )
        report.providers.append(per_provider)

    return report


def _benchmark_one(pdf_path: str, parser, ground_truth_dir: str, grade_fn) -> BenchmarkPerPdf:
    pdf_name = os.path.basename(pdf_path)
    gt_path = _ground_truth_path(ground_truth_dir, pdf_name)
    gt = _load_gt(gt_path)
    t0 = time.time()
    try:
        result = parser.parse(pdf_path)
    except ProviderNotAvailableError as e:
        return BenchmarkPerPdf(
            pdf_name=pdf_name, provider=parser.name,
            overall=0.0, hardest_required=0.0, per_field={},
            source_page_match=None, latency_ms=0, estimated_cost_usd=None,
            error=f"not_available:{e}",
        )
    except ProviderNotImplementedError as e:
        return BenchmarkPerPdf(
            pdf_name=pdf_name, provider=parser.name,
            overall=0.0, hardest_required=0.0, per_field={},
            source_page_match=None, latency_ms=0, estimated_cost_usd=None,
            error=f"not_implemented:{type(parser).__name__}",
        )
    except Exception as e:
        return BenchmarkPerPdf(
            pdf_name=pdf_name, provider=parser.name,
            overall=0.0, hardest_required=0.0, per_field={},
            source_page_match=None, latency_ms=0, estimated_cost_usd=None,
            error=f"parse_error:{type(e).__name__}:{e}",
        )
    latency_ms = int((time.time() - t0) * 1000) or result.latency_ms

    if gt is None:
        return BenchmarkPerPdf(
            pdf_name=pdf_name, provider=parser.name,
            overall=0.0, hardest_required=0.0, per_field={},
            source_page_match=None, latency_ms=latency_ms,
            estimated_cost_usd=result.estimated_cost_usd,
            error="missing_ground_truth",
        )

    extraction = _parse_result_to_extraction(result)
    score = grade_fn(extraction, gt)
    return BenchmarkPerPdf(
        pdf_name=pdf_name, provider=parser.name,
        overall=round(score.overall, 4),
        hardest_required=round(score.hardest_required, 4),
        per_field={fs.field_name: round(fs.score, 4) for fs in score.field_scores},
        source_page_match=score.source_page_match,
        latency_ms=latency_ms,
        estimated_cost_usd=result.estimated_cost_usd,
    )


def _ground_truth_path(gt_dir: str, pdf_name: str) -> str:
    base, _ = os.path.splitext(pdf_name)
    return os.path.join(gt_dir, f"{base}.json")


def _load_gt(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    return data.get("expected", data)


# ---- Markdown formatter ----------------------------------------------------

def format_benchmark_markdown(report: BenchmarkReport) -> str:
    """Render a comparison table per provider for the accuracy doc."""
    lines: list[str] = ["# Provider benchmark report", ""]
    if report.notes:
        lines += ["## Notes"]
        lines += [f"- {n}" for n in report.notes]
        lines += [""]
    lines += ["## Per-provider summary"]
    lines += [
        "| Provider | Available | Avg overall | Avg hardest-required | Total latency (ms) | Total cost (USD est.) | Skipped PDFs |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for pp in report.providers:
        if not pp.available:
            lines.append(
                f"| {pp.provider} | NO ({pp.skip_reason}) | — | — | — | — | {len(report.pdfs)} |"
            )
            continue
        errored = len([r for r in pp.per_pdf if r.error is not None])
        cost_label = (
            "$0.0000" if pp.total_cost_usd == 0
            else (f"${pp.total_cost_usd:.4f}" if pp.total_cost_usd is not None else "unknown")
        )
        if pp.unpriced_calls > 0 and pp.total_cost_usd is not None and pp.total_cost_usd > 0:
            cost_label = f"≥ {cost_label} ({pp.unpriced_calls} unpriced)"
        lines.append(
            f"| {pp.provider} | YES | "
            f"{pp.avg_overall*100:.1f}% | {pp.avg_hardest*100:.1f}% | "
            f"{pp.total_latency_ms} | {cost_label} | {errored} |"
        )
    lines += [""]
    lines += ["## Per-PDF detail"]
    for pp in report.providers:
        if not pp.available:
            continue
        lines += [f"### {pp.provider}",
                   "| PDF | overall | hardest | error |",
                   "|---|---:|---:|---|"]
        for row in pp.per_pdf:
            err = row.error or ""
            lines.append(
                f"| {row.pdf_name} | {row.overall*100:.1f}% | "
                f"{row.hardest_required*100:.1f}% | {err} |"
            )
        lines += [""]
    return "\n".join(lines)
