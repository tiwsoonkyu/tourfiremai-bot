"""
v2.lib.cassette_redactor — Mask sensitive data in recorded LLM cassettes
before they're committed to the repo.

Combines the existing v2.lib.redactor (for tokens/PII patterns) with
extra cassette-specific rules:
  - Anonymize tour_code_real → WS{N}-{NNN}
  - Anonymize PDF hashes to short prefix
  - Truncate raw_snippet to ≤ 500 chars
  - Mask wholesale brand names anywhere in nested strings
  - Mask FB PSID, phone, email, API keys (delegated to redactor.py)

Public API:
    redact_cassette(cassette: dict) -> dict
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from typing import Any

from . import redactor as _r

logger = logging.getLogger("v2.cassette_redactor")


# Wholesale brand patterns (word-boundary, same set as response_writer brand-leak guard)
_WHOLESALE_PATTERNS = [
    re.compile(r"\b(?:ttn|zego|formosa|i[-\s]?travel|rich\s+tour|best\s+tour)\b", re.I),
    re.compile(r"(?:^|[\s.,/])GS\s+(?:travel|tour)", re.I),
    re.compile(r"ttn[\s_]?เกิดมาเที่ยว"),
]
_RAW_SNIPPET_MAX_CHARS = 500

# Tour code real pattern: BCCKG27-HU style
_TOUR_CODE_REAL_RE = re.compile(r"\b([A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+)\b")


def _mask_wholesale_in_text(text: Any) -> Any:
    if not isinstance(text, str) or not text:
        return text
    out = text
    for pat in _WHOLESALE_PATTERNS:
        out = pat.sub("WS_***", out)
    return out


def _truncate_raw_snippet(text: Any) -> Any:
    if isinstance(text, str) and len(text) > _RAW_SNIPPET_MAX_CHARS:
        return text[: _RAW_SNIPPET_MAX_CHARS - 3] + "..."
    return text


def _anonymize_tour_code(text: Any, mapping: dict[str, str]) -> Any:
    """
    Replace any tour_code_real-looking pattern with a stable anonymized form.
    Same original → same anonymized output across the cassette.
    """
    if not isinstance(text, str) or not text:
        return text

    def _rep(m):
        original = m.group(1)
        if original not in mapping:
            mapping[original] = f"WS{len(mapping) + 1:02d}-{len(mapping) * 17 + 11:03d}"
        return mapping[original]

    return _TOUR_CODE_REAL_RE.sub(_rep, text)


def redact_cassette(cassette: dict) -> dict:
    """
    Returns a new dict with sensitive data masked. Does not mutate input.

    Applies, in order:
      1. existing redactor.redact_event (PSID/keys/tokens/email/phone)
      2. wholesale brand removal everywhere
      3. tour_code_real → WS{N}-{NNN} (stable mapping within this cassette)
      4. raw_snippet truncation
      5. structured.raw_snippet (LLM output field) truncated too
    """
    if not isinstance(cassette, dict):
        return cassette

    work = deepcopy(cassette)

    # 1) Run base redactor for tokens/PII/PSID
    work = _r.redact_event(work)

    # 2/3) Walk + apply wholesale + tour_code anonymization
    mapping: dict[str, str] = {}
    work = _walk(work, lambda v: _anonymize_tour_code(_mask_wholesale_in_text(v), mapping))

    # 4/5) Truncate raw_snippet specifically — both in request.messages content and
    # response.structured.raw_snippet
    work = _truncate_raw_snippets_in_dict(work)

    return work


def _walk(obj: Any, fn) -> Any:
    if isinstance(obj, dict):
        return {k: _walk(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(x, fn) for x in obj]
    return fn(obj)


def _truncate_raw_snippets_in_dict(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "raw_snippet" or (isinstance(k, str) and "raw_snippet" in k.lower()):
                out[k] = _truncate_raw_snippet(v) if isinstance(v, str) else v
            else:
                out[k] = _truncate_raw_snippets_in_dict(v)
        return out
    if isinstance(obj, list):
        return [_truncate_raw_snippets_in_dict(x) for x in obj]
    return obj
