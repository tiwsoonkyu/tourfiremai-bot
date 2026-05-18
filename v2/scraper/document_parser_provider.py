"""
v2.scraper.document_parser_provider — Optional document parser / paid OCR
provider abstraction.

Sprint 4 Phase 2 live-accuracy follow-up tasked us with preparing for an
optional paid OCR layer to improve fee accuracy on the hard PDFs (WS01 price
tables, WS03/04/05 image-heavy pages). This module provides the *interface*
and fail-closed *stubs* — it does NOT make any live paid-provider call.

Hard rules per `docs/tasks/CURRENT_DEV_TASK.md` (DEV-2026-05-19-003):
  - Paid providers must be used ON-DEMAND only — never the default path.
  - Stubs must fail closed (clear missing-provider / missing-credentials error)
    so unit tests never accidentally reach the network.
  - NO live provider calls in unit tests.
  - Bot must NOT echo a fee value when confidence is below policy threshold —
    that invariant is enforced upstream in `fee_answer_policy.py` and is NOT
    weakened by this module.

Public API:
    DocumentParserProvider           — Protocol
    DocumentParseResult              — dataclass returned by parse()
    ProviderNotAvailableError        — raised when creds / dependencies missing
    ProviderNotImplementedError      — raised by stub providers
    MockDocumentParser               — test-only provider (no network)
    MistralOCRParser                 — stub (fail-closed without API key)
    GoogleDocumentAIParser           — stub (fail-closed without GCP creds)
    AWSTextractParser                — stub (fail-closed without AWS creds)
    make_document_parser(name)       — factory
    available_providers()            — list of registered names

Provider naming convention (used in CLI / config):
    "mock"               — in-process, deterministic, no network. Default for
                           tests + benchmark when no paid creds are present.
    "mistral_ocr"        — Mistral's OCR API (good Thai support).
    "google_document_ai" — Google Document AI form parser.
    "aws_textract"       — Amazon Textract.

Adding a new provider:
    1. Subclass `DocumentParserProvider` (or fit the Protocol).
    2. Implement `is_available()` → (bool, reason). Check both:
       - that any required Python SDK is importable (fail closed on ImportError)
       - that any required env var (V2_STAGING_<PROVIDER>_*) is set
    3. Implement `parse(pdf_path, asked_field=None)` → `DocumentParseResult`.
       For a stub, raise `ProviderNotImplementedError` with a clear hint.
    4. Register in `_PROVIDERS` below.
    5. Add per-call pricing in `v2/lib/llm_pricing.py` if you know the rates.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

logger = logging.getLogger("v2.scraper.document_parser_provider")


# ---- Exceptions ------------------------------------------------------------

class ProviderError(RuntimeError):
    """Base class for all provider-side errors."""


class ProviderNotAvailableError(ProviderError):
    """Raised when a provider's credentials/SDK are missing.

    Use this for the "fail-closed" path: tests must be able to catch it
    without triggering a network call.
    """


class ProviderNotImplementedError(ProviderError):
    """Raised by stub providers when `parse` is called.

    The message must clearly identify the provider AND the activation path
    (which env var / SDK install is needed). This is what keeps stubs safe in
    unit tests: any code path that requires a live call surfaces a clear
    failure rather than silently going to the network.
    """


# ---- Result type -----------------------------------------------------------

@dataclass
class DocumentParseResult:
    """Output of a single provider parse over one PDF.

    Designed to be a superset that current regex+vision and future paid
    providers can both populate.
    """
    provider: str
    raw_text: str = ""
    tables: list[dict] = field(default_factory=list)
    fee_fields: dict[str, Optional[int]] = field(default_factory=dict)
    fee_field_confidences: dict[str, Optional[float]] = field(default_factory=dict)
    visa_status: Optional[str] = None
    source_page: Optional[int] = None
    raw_snippet: Optional[str] = None
    page_count: int = 0
    latency_ms: int = 0
    estimated_cost_usd: Optional[float] = None
    estimated_tokens_in: int = 0
    estimated_tokens_out: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "provider": self.provider,
            "raw_text_len": len(self.raw_text),
            "table_count": len(self.tables),
            "fee_fields": dict(self.fee_fields),
            "fee_field_confidences": {k: v for k, v in self.fee_field_confidences.items()},
            "visa_status": self.visa_status,
            "source_page": self.source_page,
            "page_count": self.page_count,
            "latency_ms": self.latency_ms,
            "estimated_cost_usd": self.estimated_cost_usd,
            "estimated_tokens_in": self.estimated_tokens_in,
            "estimated_tokens_out": self.estimated_tokens_out,
            "error": self.error,
        }
        return d


# ---- Protocol --------------------------------------------------------------

@runtime_checkable
class DocumentParserProvider(Protocol):
    """Provider interface contract.

    Every provider must implement these three methods:
      - `name` property: stable string id, used in CLI + reports.
      - `is_available()`: cheap probe — returns (ok, reason). MUST NOT call
        the network. Used by the benchmark runner to skip providers that
        aren't configured.
      - `parse(pdf_path, asked_field=None)`: actually do the extraction.
        Must raise `ProviderNotAvailableError` when called on a provider
        whose `is_available()` returned False.
    """
    name: str

    def is_available(self) -> tuple[bool, Optional[str]]: ...

    def parse(self, pdf_path: str, *,
              asked_field: Optional[str] = None) -> DocumentParseResult: ...


# ---- Mock provider (test-only, no network) ---------------------------------

class MockDocumentParser:
    """Deterministic in-process provider for unit tests + safe benchmark
    default. Generates synthetic but valid `DocumentParseResult` values; the
    output is keyed off `os.path.basename(pdf_path)` so multiple test calls
    against the same PDF return the same data.
    """
    name = "mock"

    # Canned output keyed by basename. Tests pin these values so the
    # benchmark grader gets predictable accuracy numbers.
    _CANNED: dict[str, dict] = {
        "WS01_jp_tour.pdf": {
            "tip_amount": 2000, "deposit_amount": 15000,
            "single_supplement": 6000, "infant_fee": 6000,
            "visa_status": "exempt",
            "source_page": 3,
        },
        "WS03_jp_tour.pdf": {
            "single_supplement": 8900, "infant_fee": 9900,
            "visa_status": "exempt",
            "source_page": 8,
        },
        # Generic fallback for any other PDF in the corpus
        "_default": {
            "tip_amount": None, "deposit_amount": None,
            "single_supplement": None, "visa_status": "exempt",
            "source_page": 1,
        },
    }

    def is_available(self) -> tuple[bool, Optional[str]]:
        return True, None

    def parse(self, pdf_path: str, *,
              asked_field: Optional[str] = None) -> DocumentParseResult:
        basename = os.path.basename(pdf_path) if pdf_path else "_default"
        data = self._CANNED.get(basename) or self._CANNED["_default"]
        fee_fields = {
            "tip_amount": data.get("tip_amount"),
            "deposit_amount": data.get("deposit_amount"),
            "single_supplement": data.get("single_supplement"),
            "visa_fee": data.get("visa_fee"),
            "infant_fee": data.get("infant_fee"),
            "child_fee_no_bed": data.get("child_fee_no_bed"),
        }
        # Conservative confidences for the mock provider: any populated
        # field gets 0.85 (regex-equivalent), so the benchmark grader can
        # exercise the policy thresholds without exceeding them.
        confs = {f"{k.split('_')[0]}_confidence": (0.85 if v is not None else None)
                  for k, v in fee_fields.items() if v is not None}
        return DocumentParseResult(
            provider=self.name,
            raw_text=f"<mock raw_text for {basename}>",
            tables=[],
            fee_fields=fee_fields,
            fee_field_confidences=confs,
            visa_status=data.get("visa_status"),
            source_page=data.get("source_page"),
            page_count=10,
            latency_ms=1,
            estimated_cost_usd=0.0,            # mock costs nothing
            estimated_tokens_in=0,
            estimated_tokens_out=0,
        )


# ---- Stub paid providers ---------------------------------------------------

class _StubPaidProvider:
    """Common fail-closed behavior for paid stubs.

    Subclasses set `name` + `_creds_env_vars` + `_sdk_import_name` (one of
    these can be empty for a credentialed-but-no-sdk or sdk-but-no-creds
    config). `is_available()` returns False (with a clear reason) when ANY
    requirement is missing; `parse()` first checks `is_available()` and
    raises `ProviderNotAvailableError`, then raises
    `ProviderNotImplementedError` (since this is a stub).
    """
    name: str = "_stub"
    _creds_env_vars: tuple[str, ...] = ()
    _sdk_import_name: Optional[str] = None
    _activation_hint: str = ""

    def is_available(self) -> tuple[bool, Optional[str]]:
        missing_env = [v for v in self._creds_env_vars if not os.environ.get(v)]
        if missing_env:
            return False, f"missing_credentials:{','.join(missing_env)}"
        if self._sdk_import_name:
            try:
                __import__(self._sdk_import_name)
            except ImportError:
                return False, f"missing_sdk:{self._sdk_import_name}"
        return True, None

    def parse(self, pdf_path: str, *,
              asked_field: Optional[str] = None) -> DocumentParseResult:
        ok, reason = self.is_available()
        if not ok:
            raise ProviderNotAvailableError(
                f"{self.name} not available: {reason}. "
                f"To activate: {self._activation_hint or '(see docstring)'}"
            )
        raise ProviderNotImplementedError(
            f"{self.name} is a STUB. Add a real `parse()` implementation in "
            f"v2/scraper/document_parser_provider.py before using it. "
            f"This protects against accidental paid calls in unit tests."
        )


class MistralOCRParser(_StubPaidProvider):
    """Stub for Mistral's OCR API. Good Thai support + table extraction."""
    name = "mistral_ocr"
    _creds_env_vars = ("V2_STAGING_MISTRAL_API_KEY",)
    _sdk_import_name = "mistralai"
    _activation_hint = (
        "1) `pip install mistralai`  "
        "2) export V2_STAGING_MISTRAL_API_KEY=...  "
        "3) implement MistralOCRParser.parse() with mistralai.Mistral(api_key=...).ocr.process(...)"
    )


class GoogleDocumentAIParser(_StubPaidProvider):
    """Stub for Google Cloud Document AI form parser."""
    name = "google_document_ai"
    _creds_env_vars = (
        "V2_STAGING_GOOGLE_AI_PROJECT_ID",
        "V2_STAGING_GOOGLE_AI_PROCESSOR_ID",
        "GOOGLE_APPLICATION_CREDENTIALS",
    )
    _sdk_import_name = "google.cloud.documentai_v1"
    _activation_hint = (
        "1) `pip install google-cloud-documentai`  "
        "2) export V2_STAGING_GOOGLE_AI_PROJECT_ID, V2_STAGING_GOOGLE_AI_PROCESSOR_ID, "
        "and GOOGLE_APPLICATION_CREDENTIALS (path to service-account JSON)  "
        "3) implement GoogleDocumentAIParser.parse()"
    )


class AWSTextractParser(_StubPaidProvider):
    """Stub for Amazon Textract."""
    name = "aws_textract"
    _creds_env_vars = (
        "V2_STAGING_AWS_ACCESS_KEY_ID",
        "V2_STAGING_AWS_SECRET_ACCESS_KEY",
        "V2_STAGING_AWS_REGION",
    )
    _sdk_import_name = "boto3"
    _activation_hint = (
        "1) `pip install boto3`  "
        "2) export V2_STAGING_AWS_ACCESS_KEY_ID, V2_STAGING_AWS_SECRET_ACCESS_KEY, "
        "V2_STAGING_AWS_REGION  "
        "3) implement AWSTextractParser.parse() with boto3.client('textract')"
    )


# ---- Registry --------------------------------------------------------------

_PROVIDERS: dict[str, type] = {
    "mock":               MockDocumentParser,
    "mistral_ocr":        MistralOCRParser,
    "google_document_ai": GoogleDocumentAIParser,
    "aws_textract":       AWSTextractParser,
}


def available_providers() -> list[str]:
    """Returns the list of registered provider names."""
    return list(_PROVIDERS.keys())


def make_document_parser(name: str, config: Any = None) -> DocumentParserProvider:
    """Factory: returns an instance of the named provider, or raises
    ValueError if the name is not registered.

    NB: This does NOT make a network call. It does NOT check
    `is_available()`. Callers should check `is_available()` before calling
    `parse()`.
    """
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ValueError(
            f"unknown document parser provider: {name!r}. "
            f"Registered: {sorted(_PROVIDERS.keys())}"
        )
    return cls()
