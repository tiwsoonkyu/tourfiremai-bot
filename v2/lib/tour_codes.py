"""
v2.lib.tour_codes — Tour code normalization & separation

CRITICAL: V1 conflated web_code (e.g. "ap242455") with tour_code_real (e.g.
"BCCKG27-HU") in a single field. V2 keeps them strictly separate.

Public API:
    normalize_web_code(text: str) -> str | None
    normalize_tour_code_real(text: str) -> str | None
    parse_airline(text: str) -> str | None
    ensure_airline_not_used_as_code(airline: str, code: str) -> None  # raises
    classify_code(text: str) -> {"kind": "web" | "tour_code_real" | "airline" | None, "value": str}
"""

from __future__ import annotations

import re
from typing import Optional

# web_code pattern from tourfiremai.com URL: /intertour/{country_id}/{name} → ap{digits}
# Observed format: lowercase prefix (2-3 chars) + 5-7 digits, e.g. ap242455, in123456
WEB_CODE_RE = re.compile(r"^[a-z]{2,3}\d{5,7}$")

# Real tour codes from PDF/system — uppercase, must have dash OR mix letters+digits.
# Examples: BCCKG27-HU, JX001, KIX-FUK-5D4N
TOUR_CODE_DASH_RE = re.compile(r"^[A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+$")        # has dash
TOUR_CODE_LETDIG_RE = re.compile(r"^[A-Z]{2,}\d{2,}[A-Z0-9]*$")           # letters+digits (JX001)
TOUR_CODE_REAL_PATTERNS = (TOUR_CODE_DASH_RE, TOUR_CODE_LETDIG_RE)
TOUR_CODE_EXTRACT_PATTERNS = (
    re.compile(r"\b([A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+)\b"),
    re.compile(r"\b([A-Z]{2,}\d{2,}[A-Z0-9]*)\b"),
)

# Known airline IATA codes (2-3 letters uppercase). Note: airline ALONE is not a tour code.
KNOWN_AIRLINES = {
    # Full-service
    "TG", "JL", "NH", "KE", "OZ", "SQ", "CI", "BR", "CX", "MU", "CA", "CZ", "VN",
    # LCC
    "VZ", "FD", "DD", "XJ", "XW", "TR", "AK", "D7", "JT", "QZ",
    # Cargo / charter
    "HU", "MF", "WE",
}

AIRLINE_RE = re.compile(r"^[A-Z]{2,3}$")


def normalize_web_code(text: str) -> Optional[str]:
    """Extract and lowercase a web_code if `text` is/contains one."""
    if not text:
        return None
    candidate = text.strip().lower()
    if WEB_CODE_RE.match(candidate):
        return candidate
    # Try to extract from longer text
    m = re.search(r"\b([a-z]{2,3}\d{5,7})\b", text.lower())
    if m:
        return m.group(1)
    return None


_WEB_CODE_UPPER_RE = re.compile(r"^[A-Z]{2,3}\d{5,7}$")


def normalize_tour_code_real(text: str) -> Optional[str]:
    """Extract and uppercase a tour_code_real if `text` is/contains one."""
    if not text:
        return None
    candidate = text.strip().upper()
    # Reject if it matches web_code pattern (short-letter-prefix + digits)
    if _WEB_CODE_UPPER_RE.match(candidate):
        return None
    for pat in TOUR_CODE_REAL_PATTERNS:
        if pat.match(candidate):
            if candidate in KNOWN_AIRLINES:
                return None
            return candidate
    # Extract from longer text
    upper_text = text.upper()
    for pat in TOUR_CODE_EXTRACT_PATTERNS:
        m = pat.search(upper_text)
        if m:
            found = m.group(1)
            if found in KNOWN_AIRLINES:
                continue
            return found
    return None


def parse_airline(text: str) -> Optional[str]:
    """Detect an airline IATA code in text."""
    if not text:
        return None
    candidate = text.strip().upper()
    if AIRLINE_RE.match(candidate) and candidate in KNOWN_AIRLINES:
        return candidate
    # Embedded form: search for any \b[A-Z]{2,3}\b token in KNOWN_AIRLINES
    for m in re.finditer(r"\b([A-Z]{2,3})\b", candidate):
        token = m.group(1)
        if token in KNOWN_AIRLINES:
            return token
    return None


class CodeMisuseError(ValueError):
    """Raised when an airline code is being used as a tour code."""


def ensure_airline_not_used_as_code(airline: Optional[str], code: Optional[str]) -> None:
    """
    Guard: if both `airline` and `code` are given, ensure `code` != `airline`.

    Used by orchestrator before lock_selected_tour() — a customer saying "HU" alone
    must not be interpreted as a tour selection (it's an airline preference).
    """
    if airline and code and airline.strip().upper() == code.strip().upper():
        raise CodeMisuseError(
            f"airline code {airline!r} cannot be used as a tour code"
        )


def classify_code(text: str) -> dict:
    """
    Classify free-form text as web_code, tour_code_real, airline, or unknown.

    Returns:
        {"kind": "web" | "tour_code_real" | "airline" | None, "value": str | None}
    """
    if not text:
        return {"kind": None, "value": None}

    # Try in order of specificity: web_code (lower+digits) is most specific.
    web = normalize_web_code(text)
    if web:
        return {"kind": "web", "value": web}

    real = normalize_tour_code_real(text)
    if real:
        return {"kind": "tour_code_real", "value": real}

    air = parse_airline(text)
    if air:
        return {"kind": "airline", "value": air}

    return {"kind": None, "value": None}
