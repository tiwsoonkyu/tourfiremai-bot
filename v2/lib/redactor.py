"""
v2.lib.redactor — Mask PII/secrets in structured logs.

Public API:
    redact(value: str) -> str       # text-level redaction
    redact_event(event: dict) -> dict  # deep redact known sensitive keys

Sensitive patterns recognized:
    - Facebook PSID (long numeric)
    - Anthropic / OpenAI API keys (sk-ant-..., sk-...)
    - FB Page Access Token (EAAR..., EAAB...)
    - LINE Channel tokens
    - Generic Bearer tokens / JWTs
    - Email addresses
    - Thai phone numbers
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

# Pattern → masker tuples. Order matters (longest specific first).
_PATTERNS = [
    # Anthropic key: sk-ant-api03-...
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "sk-ant-***REDACTED***"),
    # OpenAI key: sk-...
    (re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"), "sk-***REDACTED***"),
    # FB Page Access Token (long EAAR/EAAB/EAAG... base64-ish)
    (re.compile(r"EAA[A-Za-z0-9_]{30,}"), "EAA-***REDACTED***"),
    # LINE Channel tokens (base64-ish ~150 chars after channel ID)
    (re.compile(r"Bearer\s+[A-Za-z0-9+/=]{40,}"), "Bearer ***REDACTED***"),
    # JWT
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "***JWT-REDACTED***"),
    # Telegram bot token (digits:base64) — allow leading "bot" prefix
    (re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}"), "***TG-BOT-TOKEN-REDACTED***"),
    # Email
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "***EMAIL-REDACTED***"),
    # Thai phone (with or without country code)
    (re.compile(r"\b(?:\+?66|0)\d{8,9}\b"), "***PHONE-REDACTED***"),
]

# Long numeric IDs (FB PSID is 15-17 digits) — only redact when value-only
_PSID_RE = re.compile(r"^\d{12,20}$")

# JSON keys whose values must ALWAYS be masked, regardless of pattern match
_SENSITIVE_KEYS = {
    "password", "passwd", "pwd", "secret", "api_key", "apikey",
    "access_token", "token", "private_key", "service_key",
    "supabase_db_password", "fb_app_secret", "fb_page_access_token",
    "openai_api_key", "anthropic_api_key", "line_channel_token",
    "x-api-key", "authorization",
}


def _mask_psid(psid: str) -> str:
    """Show first 4 + last 2 chars only: 1234567890 → 1234******90."""
    if not psid or len(psid) < 8:
        return "***PSID***"
    return f"{psid[:4]}{'*' * (len(psid) - 6)}{psid[-2:]}"


def redact(text: Any) -> Any:
    """Return string with sensitive substrings replaced."""
    if not isinstance(text, str) or not text:
        return text
    out = text
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


def redact_event(obj: Any, _depth: int = 0) -> Any:
    """Recursively redact dict/list/str. Limits depth to avoid runaway."""
    if _depth > 20:
        return obj  # paranoia
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in _SENSITIVE_KEYS:
                out[k] = "***REDACTED***"
            elif kl in ("psid", "sender_id"):
                # Mask but keep some entropy for debugging
                out[k] = _mask_psid(str(v)) if isinstance(v, (str, int)) else v
            else:
                out[k] = redact_event(v, _depth + 1)
        return out
    if isinstance(obj, list):
        return [redact_event(x, _depth + 1) for x in obj]
    if isinstance(obj, str):
        # If the whole string is a numeric PSID, mask it
        if _PSID_RE.match(obj):
            return _mask_psid(obj)
        return redact(obj)
    return obj
