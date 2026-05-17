"""
v2.scraper.extraction_accuracy — Grade ExtractionResult against ground truth.

Loads a JSON ground-truth file, compares to an ExtractionResult, and reports
per-field scores + a per-PDF summary. Aggregates produce the Sprint 4
extraction accuracy report.

Public API:
    grade_extraction(result, ground_truth) -> AccuracyScore
    run_accuracy_corpus(pipeline_results, fixtures_dir) -> CorpusReport
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from .extract_fees import ExtractionResult

logger = logging.getLogger("v2.extraction_accuracy")


# Numeric fields graded with tolerance
_NUMERIC_FIELDS = (
    "tip_amount", "deposit_amount", "single_supplement",
    "visa_fee", "infant_fee", "child_fee_no_bed", "joinland_price",
)
_EXACT_FIELDS = ("visa_status",)
_BONUS_FIELDS = ("source_page", "mandatory_fees_summary")
_HARDEST_REQUIRED = ("tip_amount", "deposit_amount", "single_supplement")

_TOLERANCE = 0.10  # ±10% for numeric fields


@dataclass
class FieldScore:
    field_name: str
    expected: Any
    actual: Any
    score: float
    rule: str         # 'exact' | 'within_tolerance' | 'mismatch' | 'both_null' | 'expected_null_got_value' | 'expected_value_got_null'


@dataclass
class AccuracyScore:
    pdf_name: str
    overall: float          # 0..1, mean of all graded fields
    hardest_required: float  # mean of tip+deposit+single
    field_scores: list[FieldScore] = field(default_factory=list)
    source_page_match: Optional[bool] = None
    extraction_method: str = ""
    extraction_confidence: float = 0.0
    notes: str = ""


@dataclass
class CorpusReport:
    total: int
    per_pdf: list[AccuracyScore]
    avg_overall: float
    avg_hardest_required: float
    avg_per_field: dict[str, float]
    method_breakdown: dict[str, int]   # extraction_method → count
    confidence_distribution: list[float]
    missing_ground_truth: list[str] = field(default_factory=list)


def _grade_numeric(expected, actual) -> FieldScore:
    if expected is None and actual is None:
        return FieldScore("", expected, actual, 1.0, "both_null")
    if expected is None and actual is not None:
        # We had no expected value but extractor produced one — neutral
        return FieldScore("", expected, actual, 0.5, "expected_null_got_value")
    if expected is not None and actual is None:
        return FieldScore("", expected, actual, 0.0, "expected_value_got_null")
    try:
        e = int(expected)
        a = int(actual)
    except (TypeError, ValueError):
        return FieldScore("", expected, actual, 0.0, "type_error")
    if e == a:
        return FieldScore("", expected, actual, 1.0, "exact")
    if e == 0:
        return FieldScore("", expected, actual, 0.0, "mismatch")
    if abs(a - e) / abs(e) <= _TOLERANCE:
        return FieldScore("", expected, actual, 0.8, "within_tolerance")
    return FieldScore("", expected, actual, 0.0, "mismatch")


def _grade_string(expected, actual) -> FieldScore:
    if expected is None and actual is None:
        return FieldScore("", expected, actual, 1.0, "both_null")
    if expected is None:
        return FieldScore("", expected, actual, 0.5, "expected_null_got_value")
    if actual is None:
        return FieldScore("", expected, actual, 0.0, "expected_value_got_null")
    if str(expected).strip().lower() == str(actual).strip().lower():
        return FieldScore("", expected, actual, 1.0, "exact")
    return FieldScore("", expected, actual, 0.0, "mismatch")


def grade_extraction(result: ExtractionResult, ground_truth: dict) -> AccuracyScore:
    """
    Grade an ExtractionResult against a ground-truth dict. Returns AccuracyScore.
    """
    expected = ground_truth.get("expected", {})
    pdf_name = ground_truth.get("pdf_filename", "unknown")

    field_scores: list[FieldScore] = []

    for fname in _NUMERIC_FIELDS:
        fs = _grade_numeric(expected.get(fname), getattr(result, fname, None))
        fs.field_name = fname
        field_scores.append(fs)

    for fname in _EXACT_FIELDS:
        fs = _grade_string(expected.get(fname), getattr(result, fname, None))
        fs.field_name = fname
        field_scores.append(fs)

    overall = sum(fs.score for fs in field_scores) / max(1, len(field_scores))
    hardest = [fs.score for fs in field_scores if fs.field_name in _HARDEST_REQUIRED]
    hardest_avg = sum(hardest) / max(1, len(hardest))

    src_match: Optional[bool] = None
    expected_page = ground_truth.get("expected_source_page")
    if expected_page is not None and result.source_page is not None:
        src_match = (int(expected_page) == int(result.source_page))

    return AccuracyScore(
        pdf_name=pdf_name,
        overall=round(overall, 3),
        hardest_required=round(hardest_avg, 3),
        field_scores=field_scores,
        source_page_match=src_match,
        extraction_method=result.extraction_method,
        extraction_confidence=result.extraction_confidence,
    )


def load_ground_truth(fixtures_dir: str, pdf_basename: str) -> Optional[dict]:
    """Load `<pdf_basename>.json` from fixtures_dir."""
    base = os.path.splitext(pdf_basename)[0]
    path = os.path.join(fixtures_dir, f"{base}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_accuracy_corpus(
    pipeline_results: list[tuple[str, ExtractionResult]],
    fixtures_dir: str,
) -> CorpusReport:
    """
    pipeline_results: [(pdf_basename, ExtractionResult), ...]
    fixtures_dir:     v2/tests/fixtures/ground_truth/

    Returns CorpusReport with per-PDF scores + aggregates.
    """
    per_pdf: list[AccuracyScore] = []
    missing: list[str] = []
    method_breakdown: dict[str, int] = {}
    confidence_distribution: list[float] = []
    per_field_scores: dict[str, list[float]] = {f: [] for f in _NUMERIC_FIELDS + _EXACT_FIELDS}

    for pdf_basename, result in pipeline_results:
        gt = load_ground_truth(fixtures_dir, pdf_basename)
        if gt is None:
            missing.append(pdf_basename)
            continue
        score = grade_extraction(result, gt)
        per_pdf.append(score)
        method_breakdown[result.extraction_method] = method_breakdown.get(result.extraction_method, 0) + 1
        confidence_distribution.append(result.extraction_confidence)
        for fs in score.field_scores:
            per_field_scores[fs.field_name].append(fs.score)

    avg_per_field = {
        fname: round(sum(vs) / len(vs), 3) if vs else 0.0
        for fname, vs in per_field_scores.items()
    }
    avg_overall = round(sum(s.overall for s in per_pdf) / max(1, len(per_pdf)), 3)
    avg_hardest = round(sum(s.hardest_required for s in per_pdf) / max(1, len(per_pdf)), 3)

    return CorpusReport(
        total=len(per_pdf),
        per_pdf=per_pdf,
        avg_overall=avg_overall,
        avg_hardest_required=avg_hardest,
        avg_per_field=avg_per_field,
        method_breakdown=method_breakdown,
        confidence_distribution=confidence_distribution,
        missing_ground_truth=missing,
    )


def format_report_markdown(report: CorpusReport) -> str:
    """Render CorpusReport as a Markdown table (for SPRINT_4_ACCURACY_REPORT.md)."""
    lines = [
        "# Sprint 4 PDF Corpus Accuracy Report",
        "",
        f"**Total PDFs graded:** {report.total}",
        f"**Avg overall accuracy:** {report.avg_overall:.1%}",
        f"**Avg hardest-required (tip + deposit + single):** {report.avg_hardest_required:.1%}",
        "",
        "## Per-PDF",
        "| PDF | Overall | Hardest required | Method | Confidence | Source page match |",
        "|-----|---------|------------------|--------|-----------|-------------------|",
    ]
    for s in report.per_pdf:
        page = "✅" if s.source_page_match else ("❌" if s.source_page_match is False else "—")
        lines.append(
            f"| {s.pdf_name} | {s.overall:.0%} | {s.hardest_required:.0%} | "
            f"{s.extraction_method} | {s.extraction_confidence:.2f} | {page} |"
        )

    lines += [
        "",
        "## Per-field accuracy",
        "| Field | Accuracy |",
        "|-------|---------|",
    ]
    for fname, score in report.avg_per_field.items():
        lines.append(f"| {fname} | {score:.0%} |")

    lines += [
        "",
        "## Method breakdown",
        "| Method | Count |",
        "|--------|------|",
    ]
    for m, c in report.method_breakdown.items():
        lines.append(f"| {m} | {c} |")

    if report.missing_ground_truth:
        lines += [
            "",
            "## Missing ground truth",
            "These PDFs were extracted but no ground-truth JSON exists:",
            "",
        ]
        for p in report.missing_ground_truth:
            lines.append(f"- `{p}`")

    return "\n".join(lines) + "\n"
