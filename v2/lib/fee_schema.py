"""
v2.lib.fee_schema — Strict JSON schema for tour-fee extraction.

Codex / QA flagged a Phase 2 blocker: production code at 3 call sites passed
`response_format={"type": "json_schema", "json_schema": {"name": "TourFees",
"strict": True}}` WITHOUT a `schema` body. OpenAI's strict mode requires the
full schema to be present at request time; the prompt-file YAML schema is
documentation, not transport. Without this body, the API can return arbitrary
shapes — or, worse, silently relax `strict` mode.

This module is the single source of truth. The schema mirrors the YAML in
`v2/prompts/fee_extractor_{text,vision}_v1.md` exactly (verified field-for-field).

Public API:
    TOUR_FEES_JSON_SCHEMA         — the raw `schema` dict (object, properties, etc.)
    TOUR_FEES_REQUIRED_FIELDS     — frozenset of 13 required field names
    build_response_format()       — convenience: returns the full `response_format`
                                     dict ready to pass to llm.chat / llm.vision.

Compatibility: OpenAI Responses API + Structured Outputs format (`type` = list
in `properties` to allow nullable values; `additionalProperties: False`; `enum`
includes both literal strings and JSON `null` for `visa_status`).
"""

from __future__ import annotations

from typing import Any


# 13 fields, all required (per strict mode). Nullable values are expressed
# via `type: ["integer"|"string", "null"]` rather than via `nullable: true`,
# matching OpenAI Structured Outputs.
TOUR_FEES_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "tip_amount",
        "visa_fee",
        "visa_status",
        "single_supplement",
        "infant_fee",
        "child_fee_no_bed",
        "deposit_amount",
        "joinland_price",
        "mandatory_fees_summary",
        "extraction_confidence",
        "source_page",
        "raw_snippet",
        "notes",
    ],
    "properties": {
        "tip_amount": {
            "type": ["integer", "null"],
            "description": "Total tip in THB, per person.",
        },
        "visa_fee": {
            "type": ["integer", "null"],
            "description": "Visa fee in THB, per person.",
        },
        "visa_status": {
            "type": ["string", "null"],
            "enum": ["exempt", "required", "on_arrival", "evisa", "unknown", None],
            "description": "Whether visa is needed and how it is obtained.",
        },
        "single_supplement": {
            "type": ["integer", "null"],
            "description": "Single-supplement upcharge in THB.",
        },
        "infant_fee": {
            "type": ["integer", "null"],
            "description": "Infant (no seat) fee in THB.",
        },
        "child_fee_no_bed": {
            "type": ["integer", "null"],
            "description": "Child no-bed discount/fee in THB.",
        },
        "deposit_amount": {
            "type": ["integer", "null"],
            "description": "Initial deposit in THB.",
        },
        "joinland_price": {
            "type": ["integer", "null"],
            "description": "Land-only price in THB if quoted.",
        },
        "mandatory_fees_summary": {
            "type": ["string", "null"],
            "description": "Concise one-liner summary of all mandatory extras.",
        },
        "extraction_confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "Extractor's confidence on the whole row, 0..1.",
        },
        "source_page": {
            "type": ["integer", "null"],
            "description": "1-indexed page where fees appear.",
        },
        "raw_snippet": {
            "type": ["string", "null"],
            "description": "~200-500 char window of the fee section text.",
        },
        "notes": {
            "type": "string",
            "description": "Brief note re any ambiguity.",
        },
    },
}

# Frozenset of required field names — useful for tests + audit checks.
TOUR_FEES_REQUIRED_FIELDS: frozenset[str] = frozenset(TOUR_FEES_JSON_SCHEMA["required"])


def build_response_format(name: str = "TourFees", *, strict: bool = True) -> dict[str, Any]:
    """
    Return a `response_format` dict ready to pass to llm.chat / llm.vision.

    Args:
        name:   the JSON schema name (kept stable as 'TourFees' for cassette
                replay determinism — changing it busts existing cassettes).
        strict: OpenAI strict-mode flag. Default True; flip only for ad-hoc
                debugging.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": strict,
            "schema": TOUR_FEES_JSON_SCHEMA,
        },
    }
