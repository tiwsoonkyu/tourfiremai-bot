"""Sprint 4 test: extraction_accuracy framework."""

import json
import os
import pytest

from v2.scraper.extract_fees import ExtractionResult
from v2.scraper.extraction_accuracy import (
    grade_extraction, run_accuracy_corpus, _grade_numeric, _grade_string,
    format_report_markdown,
)


class TestGradeNumeric:
    def test_both_null(self):
        s = _grade_numeric(None, None)
        assert s.score == 1.0 and s.rule == "both_null"

    def test_exact_match(self):
        s = _grade_numeric(1500, 1500)
        assert s.score == 1.0 and s.rule == "exact"

    def test_within_tolerance(self):
        s = _grade_numeric(1500, 1550)
        assert s.score == 0.8 and s.rule == "within_tolerance"

    def test_outside_tolerance(self):
        s = _grade_numeric(1500, 2000)
        assert s.score == 0.0 and s.rule == "mismatch"

    def test_expected_value_actual_null(self):
        s = _grade_numeric(1500, None)
        assert s.score == 0.0 and s.rule == "expected_value_got_null"

    def test_expected_null_actual_value(self):
        s = _grade_numeric(None, 1500)
        assert s.score == 0.5 and s.rule == "expected_null_got_value"


class TestGradeString:
    def test_exact_match(self):
        s = _grade_string("exempt", "exempt")
        assert s.score == 1.0

    def test_case_insensitive(self):
        s = _grade_string("Exempt", "exempt")
        assert s.score == 1.0

    def test_mismatch(self):
        s = _grade_string("exempt", "required")
        assert s.score == 0.0

    def test_both_null(self):
        s = _grade_string(None, None)
        assert s.score == 1.0


class TestGradeExtraction:
    def test_perfect_score(self):
        gt = {
            "pdf_filename": "perfect.pdf",
            "expected": {
                "tip_amount": 1500, "deposit_amount": 10000,
                "single_supplement": 5500, "visa_status": "exempt",
                "infant_fee": 4500, "child_fee_no_bed": None,
                "visa_fee": None, "joinland_price": None,
            },
            "expected_source_page": 4,
        }
        result = ExtractionResult(
            tip_amount=1500, deposit_amount=10000, single_supplement=5500,
            visa_status="exempt", infant_fee=4500,
            extraction_method="pdfplumber+regex", extraction_confidence=0.92,
            source_page=4,
        )
        score = grade_extraction(result, gt)
        assert score.overall == 1.0
        assert score.hardest_required == 1.0
        assert score.source_page_match is True

    def test_partial_score(self):
        gt = {
            "pdf_filename": "partial.pdf",
            "expected": {
                "tip_amount": 1500, "deposit_amount": 10000,
                "single_supplement": 5500, "visa_status": "exempt",
                "infant_fee": 4500, "child_fee_no_bed": None,
                "visa_fee": None, "joinland_price": None,
            },
        }
        # Only got tip right; rest null
        result = ExtractionResult(tip_amount=1500, extraction_method="regex")
        score = grade_extraction(result, gt)
        assert 0 < score.overall < 1.0
        assert score.hardest_required > 0  # tip is 1/3 of hardest


class TestCorpusReport:
    def test_aggregation(self, tmp_path):
        gt_dir = tmp_path / "ground_truth"
        gt_dir.mkdir()
        # Write 2 ground truths
        (gt_dir / "p1.json").write_text(json.dumps({
            "pdf_filename": "p1.pdf",
            "expected": {"tip_amount": 1500, "deposit_amount": 10000,
                         "single_supplement": 5500, "visa_status": "exempt"},
        }))
        (gt_dir / "p2.json").write_text(json.dumps({
            "pdf_filename": "p2.pdf",
            "expected": {"tip_amount": 2000, "deposit_amount": 8000,
                         "single_supplement": 4000, "visa_status": "required",
                         "visa_fee": 1500},
        }))

        results = [
            ("p1.pdf", ExtractionResult(tip_amount=1500, deposit_amount=10000,
                                          single_supplement=5500, visa_status="exempt",
                                          extraction_method="pdfplumber+regex",
                                          extraction_confidence=0.9)),
            ("p2.pdf", ExtractionResult(tip_amount=2000, deposit_amount=8000,
                                          single_supplement=4000, visa_status="required",
                                          visa_fee=1500,
                                          extraction_method="llm_text",
                                          extraction_confidence=0.7)),
        ]
        report = run_accuracy_corpus(results, str(gt_dir))
        assert report.total == 2
        assert report.avg_hardest_required > 0.9
        assert "pdfplumber+regex" in report.method_breakdown
        assert "llm_text" in report.method_breakdown

    def test_missing_ground_truth(self, tmp_path):
        gt_dir = tmp_path / "ground_truth"
        gt_dir.mkdir()
        results = [("nogt.pdf", ExtractionResult())]
        report = run_accuracy_corpus(results, str(gt_dir))
        assert "nogt.pdf" in report.missing_ground_truth
        assert report.total == 0


class TestFormatReport:
    def test_markdown_contains_summary_lines(self, tmp_path):
        from v2.scraper.extraction_accuracy import CorpusReport, AccuracyScore
        report = CorpusReport(
            total=1, per_pdf=[
                AccuracyScore(pdf_name="x.pdf", overall=0.95, hardest_required=1.0,
                              extraction_method="pdfplumber+regex",
                              extraction_confidence=0.9, source_page_match=True)
            ],
            avg_overall=0.95, avg_hardest_required=1.0,
            avg_per_field={"tip_amount": 1.0, "visa_status": 1.0},
            method_breakdown={"pdfplumber+regex": 1},
            confidence_distribution=[0.9],
        )
        md = format_report_markdown(report)
        assert "Sprint 4 PDF Corpus Accuracy" in md
        assert "x.pdf" in md
        assert "pdfplumber+regex" in md
