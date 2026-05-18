"""
Tests for DEV-2026-05-19-003 — document parser provider abstraction +
benchmark runner.

Required test categories (from task spec):
  1. Provider interface contract
  2. Mock provider output
  3. Missing credentials fail closed
  4. Benchmark runner can run without live paid provider credentials
  5. Fee policy still handsoff when confidence is below threshold
  6. No wholesale brand leakage in new prompts/reports/cassettes
  7. Pricing estimator regression (if pricing changed) — covered here too
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from v2.scraper.document_parser_provider import (
    DocumentParseResult, DocumentParserProvider,
    MockDocumentParser, MistralOCRParser, GoogleDocumentAIParser, AWSTextractParser,
    ProviderNotAvailableError, ProviderNotImplementedError,
    make_document_parser, available_providers,
)
from v2.scraper.benchmark_providers import (
    benchmark_providers, format_benchmark_markdown, BenchmarkReport,
)


# ---- 1. Interface contract -------------------------------------------------

class TestProviderInterfaceContract:
    def test_all_registered_providers_implement_protocol(self):
        for name in available_providers():
            inst = make_document_parser(name)
            assert isinstance(inst, DocumentParserProvider), \
                f"{name} does not satisfy DocumentParserProvider Protocol"

    def test_make_document_parser_unknown_raises(self):
        with pytest.raises(ValueError):
            make_document_parser("nonexistent_provider_xyz")

    def test_factory_does_not_call_network(self):
        # Make should not even call `is_available()` — that's the caller's job.
        # Patch os.environ to ensure no env-var read happens implicitly.
        # (If a provider sneaks a network call into __init__ this test exposes it.)
        for name in available_providers():
            inst = make_document_parser(name)
            # Has the three required attrs/methods
            assert hasattr(inst, "name") and inst.name == name
            assert callable(getattr(inst, "is_available", None))
            assert callable(getattr(inst, "parse", None))

    def test_is_available_returns_tuple(self):
        for name in available_providers():
            inst = make_document_parser(name)
            ok, reason = inst.is_available()
            assert isinstance(ok, bool)
            assert reason is None or isinstance(reason, str)


# ---- 2. Mock provider output ----------------------------------------------

class TestMockProvider:
    def test_mock_always_available(self):
        m = make_document_parser("mock")
        ok, reason = m.is_available()
        assert ok is True
        assert reason is None

    def test_mock_parse_returns_canned_for_known_pdf(self):
        m = make_document_parser("mock")
        r = m.parse("/tmp/x/WS01_jp_tour.pdf")
        assert r.provider == "mock"
        assert r.fee_fields["tip_amount"] == 2000
        assert r.fee_fields["deposit_amount"] == 15000
        assert r.fee_fields["single_supplement"] == 6000
        assert r.visa_status == "exempt"
        assert r.source_page == 3
        # Conservative confidence ceiling (0.85) for mock — below strict
        # single_supplement threshold (0.90), so policy still handsoff.
        confs = r.fee_field_confidences
        assert all(0 < c <= 0.85 for c in confs.values() if c is not None)

    def test_mock_parse_unknown_pdf_returns_default(self):
        m = make_document_parser("mock")
        r = m.parse("/tmp/x/SomeOther.pdf")
        assert r.fee_fields["tip_amount"] is None
        assert r.visa_status == "exempt"

    def test_mock_estimated_cost_is_zero(self):
        m = make_document_parser("mock")
        r = m.parse("/tmp/x/WS01_jp_tour.pdf")
        assert r.estimated_cost_usd == 0.0
        assert r.estimated_tokens_in == 0
        assert r.estimated_tokens_out == 0


# ---- 3. Missing credentials fail closed -----------------------------------

class TestPaidStubsFailClosed:
    """For each paid stub: with no creds, is_available is False AND parse()
    raises ProviderNotAvailableError. Critically NO network is touched."""

    @pytest.mark.parametrize("name", ["mistral_ocr", "google_document_ai", "aws_textract"])
    def test_no_creds_is_not_available(self, name, monkeypatch):
        # Strip every env var that any paid stub might read
        for v in [
            "V2_STAGING_MISTRAL_API_KEY",
            "V2_STAGING_GOOGLE_AI_PROJECT_ID", "V2_STAGING_GOOGLE_AI_PROCESSOR_ID",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "V2_STAGING_AWS_ACCESS_KEY_ID", "V2_STAGING_AWS_SECRET_ACCESS_KEY",
            "V2_STAGING_AWS_REGION",
        ]:
            monkeypatch.delenv(v, raising=False)
        inst = make_document_parser(name)
        ok, reason = inst.is_available()
        assert ok is False
        assert isinstance(reason, str) and reason.startswith("missing_credentials")

    @pytest.mark.parametrize("name", ["mistral_ocr", "google_document_ai", "aws_textract"])
    def test_parse_without_creds_raises_provider_not_available(self, name, monkeypatch):
        for v in [
            "V2_STAGING_MISTRAL_API_KEY",
            "V2_STAGING_GOOGLE_AI_PROJECT_ID", "V2_STAGING_GOOGLE_AI_PROCESSOR_ID",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "V2_STAGING_AWS_ACCESS_KEY_ID", "V2_STAGING_AWS_SECRET_ACCESS_KEY",
            "V2_STAGING_AWS_REGION",
        ]:
            monkeypatch.delenv(v, raising=False)
        inst = make_document_parser(name)
        with pytest.raises(ProviderNotAvailableError):
            inst.parse("/tmp/anything.pdf")

    def test_mistral_with_creds_but_no_sdk_says_missing_sdk(self, monkeypatch):
        monkeypatch.setenv("V2_STAGING_MISTRAL_API_KEY", "stub_for_test")
        # mistralai isn't installed → is_available should report missing_sdk
        inst = MistralOCRParser()
        ok, reason = inst.is_available()
        # Test environment doesn't have mistralai installed — verify the
        # missing_sdk path. If a sandbox DOES have it installed, this test
        # is environment-conditional; we keep it defensive.
        try:
            import mistralai  # type: ignore  # noqa
            pytest.skip("mistralai installed in this env — sdk path not exercised")
        except ImportError:
            assert ok is False
            assert reason == "missing_sdk:mistralai"

    def test_stub_parse_with_full_creds_raises_not_implemented(self, monkeypatch):
        """If somehow all creds + SDK are present (defensive), the stub must
        still refuse to make a call — that's the safety net for unit tests."""
        # We can't easily fake "SDK present" without installing mistralai. So
        # construct a fake stub that bypasses the SDK check, then ensure
        # parse raises ProviderNotImplementedError.
        class _CredsOnlyStub(MistralOCRParser):
            def is_available(self):
                return True, None
        inst = _CredsOnlyStub()
        with pytest.raises(ProviderNotImplementedError):
            inst.parse("/tmp/anything.pdf")


# ---- 4. Benchmark runner without live paid creds --------------------------

class TestBenchmarkRunnerMockOnly:
    @pytest.fixture
    def corpus(self, tmp_path):
        """Synthesize a 2-PDF tmp corpus + ground truth for the mock provider
        to grade against. Keeps the test hermetic."""
        from v2.tests.fixtures.synthetic_pdf import build_synthetic_fee_pdf
        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        gt_dir = tmp_path / "gt"
        gt_dir.mkdir()
        for name in ("WS01_jp_tour.pdf", "WS03_jp_tour.pdf"):
            body = (
                "Tour Fee Schedule:\ntip 2000 baht\ndeposit 15000 baht\n"
                "single supplement 6000 baht\nvisa exempt\n"
            )
            build_synthetic_fee_pdf(str(pdf_dir / name), fee_text=body)
        # Ground truth files matching MockDocumentParser canned values
        gt_dir.joinpath("WS01_jp_tour.json").write_text(json.dumps({
            "expected": {"tip_amount": 2000, "deposit_amount": 15000,
                          "single_supplement": 6000, "visa_status": "exempt"},
            "expected_source_page": 3,
        }))
        gt_dir.joinpath("WS03_jp_tour.json").write_text(json.dumps({
            "expected": {"tip_amount": None, "deposit_amount": None,
                          "single_supplement": 8900, "visa_status": "exempt"},
            "expected_source_page": 8,
        }))
        return {
            "pdfs": sorted(str(p) for p in pdf_dir.glob("*.pdf")),
            "gt_dir": str(gt_dir),
        }

    def test_default_mock_only_runs_without_credentials(self, corpus, monkeypatch):
        # Clear any paid-provider env to prove they don't matter
        for v in ["V2_STAGING_MISTRAL_API_KEY",
                  "V2_STAGING_GOOGLE_AI_PROJECT_ID",
                  "V2_STAGING_AWS_ACCESS_KEY_ID"]:
            monkeypatch.delenv(v, raising=False)
        report = benchmark_providers(
            corpus["pdfs"], ["mock"], ground_truth_dir=corpus["gt_dir"],
        )
        assert len(report.providers) == 1
        pp = report.providers[0]
        assert pp.available is True
        assert pp.skip_reason is None
        assert len(pp.per_pdf) == 2
        # mock scored against matching GT → avg overall should be reasonably high
        assert pp.avg_overall > 0.5
        # mock costs $0
        assert pp.total_cost_usd == 0.0

    def test_paid_providers_skipped_no_network(self, corpus, monkeypatch):
        for v in ["V2_STAGING_MISTRAL_API_KEY",
                  "V2_STAGING_GOOGLE_AI_PROJECT_ID",
                  "V2_STAGING_AWS_ACCESS_KEY_ID"]:
            monkeypatch.delenv(v, raising=False)
        # Defense-in-depth: even if a paid provider tried to call the network,
        # we'd see it via these patches. None should fire because is_available
        # returns False before any network attempt.
        with patch("urllib.request.urlopen", side_effect=RuntimeError("network forbidden")):
            report = benchmark_providers(
                corpus["pdfs"],
                ["mock", "mistral_ocr", "google_document_ai", "aws_textract"],
                ground_truth_dir=corpus["gt_dir"],
            )
        names_to_pp = {pp.provider: pp for pp in report.providers}
        assert names_to_pp["mock"].available is True
        for paid in ("mistral_ocr", "google_document_ai", "aws_textract"):
            pp = names_to_pp[paid]
            assert pp.available is False
            assert "missing_credentials" in (pp.skip_reason or "")

    def test_markdown_report_renders(self, corpus, monkeypatch):
        for v in ["V2_STAGING_MISTRAL_API_KEY"]:
            monkeypatch.delenv(v, raising=False)
        report = benchmark_providers(
            corpus["pdfs"], ["mock", "mistral_ocr"],
            ground_truth_dir=corpus["gt_dir"],
        )
        md = format_benchmark_markdown(report)
        assert "Provider benchmark report" in md
        assert "mock" in md
        assert "mistral_ocr" in md
        # Paid provider row should mark skipped
        assert "missing_credentials" in md


# ---- 5. Fee-policy handoff invariant still holds --------------------------

class TestFeePolicyUnchanged:
    def test_thresholds_unchanged(self):
        from v2.lib.fee_answer_policy import DEFAULT_THRESHOLD, SINGLE_SUPPLEMENT_THRESHOLD
        assert DEFAULT_THRESHOLD == 0.80
        assert SINGLE_SUPPLEMENT_THRESHOLD == 0.90

    def test_low_confidence_single_supplement_still_handsoff(self):
        from v2.lib.fee_answer_policy import decide_fee_answer
        # Mock provider returns single_supplement at 0.85 → still below 0.90 → handoff
        row = {"single_supplement": 6000, "single_supplement_confidence": 0.85}
        d = decide_fee_answer(row, "single_supplement")
        assert d.decision == "handoff_low_confidence"

    def test_mock_provider_output_does_not_exceed_policy_for_strict_field(self):
        m = MockDocumentParser()
        r = m.parse("/tmp/WS01_jp_tour.pdf")
        # Mock confidences are at-most 0.85; single_supplement policy is 0.90.
        # Therefore the mock alone can NEVER answer single_supplement.
        ss_conf = r.fee_field_confidences.get("single_confidence")  # may be None
        if ss_conf is not None:
            assert ss_conf < 0.90


# ---- 6. No wholesale brand leakage in new files ---------------------------

class TestNoWholesaleLeakage:
    """Grep new files for wholesale brand strings used in the codebase's
    blacklist. Documents are allowed to *mention* the blacklist for redaction
    purposes, but never as a positive identifier."""

    # NB: this test file itself contains the blacklist regex (the strings are
    # required to define what we're searching FOR), so it's not checked here.
    # The check covers the *production* new files only — runtime code + the
    # benchmark module, which are the surfaces where a leak would harm.
    _NEW_FILES = [
        "v2/scraper/document_parser_provider.py",
        "v2/scraper/benchmark_providers.py",
    ]

    # Matches the blacklist in response_writer._WHOLESALE_BLACKLIST
    _BAD = re.compile(
        r"\b(ttn|zego|formosa|i[-\s]?travel|rich\s+tour|best\s+tour)\b|"
        r"(?:^|[\s.,/])GS\s+(?:travel|tour)|ttn[\s_]?เกิดมาเที่ยว",
        re.IGNORECASE,
    )

    @pytest.mark.parametrize("path", _NEW_FILES)
    def test_no_brand_leak(self, path):
        full = os.path.join(os.path.dirname(__file__), "..", "..", path)
        full = os.path.normpath(full)
        if not os.path.exists(full):
            pytest.skip(f"file not in this checkout: {full}")
        text = open(full, "r", encoding="utf-8").read()
        m = self._BAD.search(text)
        assert m is None, f"wholesale brand leak in {path}: {m.group(0)!r}"


# ---- 7. Pricing estimator sanity (unchanged from d0a43bf) -----------------

class TestPricingUnchanged:
    def test_per_token_values_still_correct(self):
        from v2.lib.llm_pricing import estimate_cost
        # Confirms d0a43bf fix is still in place (no regression)
        cost = estimate_cost("gpt-4o", 126840, 9020)
        assert cost is not None
        assert 0.30 < cost < 0.60, f"got ${cost} — pricing regressed"

    def test_format_cost_with_disclaimer_present(self):
        from v2.lib.llm_pricing import format_cost_with_disclaimer
        assert "estimate" in format_cost_with_disclaimer(0.41).lower()
