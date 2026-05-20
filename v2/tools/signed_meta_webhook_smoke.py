"""
v2.tools.signed_meta_webhook_smoke — DEV-2026-05-20-016 (Sprint 5 Package J).

Offline-safe helper that builds a Meta Messenger-shaped event, signs it with
HMAC-SHA256 (the same algorithm the V2 webhook verifies), and either prints
a redacted curl command (default) or POSTs the signed payload to a local /
staging URL that the operator passes in explicitly.

Hard rules (enforced by ``v2/tests/test_signed_meta_webhook_smoke.py``):

    - Dry-run by default. No HTTP request is sent unless the operator
      passes ``--post-url`` AND ``--i-understand-staging-only``.
    - Never calls Meta / LINE / OpenAI / OCR / paid-provider endpoints —
      the only outbound POST is to a URL the operator types in (intended
      for ``http://localhost:5000/webhook`` or the V2 staging webhook).
    - The app secret is read from ``V2_STAGING_FB_APP_SECRET`` env (or a
      ``--app-secret-env <NAME>`` override) and is NEVER printed.
    - The signed body contains only the operator-provided PSID and an
      operator-provided text. Both are echoed at full length in the curl
      preview by design (so the operator can replay the exact request),
      but the dry-run output redacts the signature digest to its first 8
      hex chars + ``...`` to avoid pasting a long secret-derived token in
      shared screens.
    - The helper never touches Supabase, Redis, or DB clients.

Usage::

    # Print a redacted, signed curl command (default — no network IO):
    python -m v2.tools.signed_meta_webhook_smoke \\
        --psid 11112222333344445555 \\
        --text "smoke test"

    # Actually POST to a local / staging URL (explicit two-flag opt-in):
    python -m v2.tools.signed_meta_webhook_smoke \\
        --psid 11112222333344445555 \\
        --text "smoke test" \\
        --post-url http://localhost:5000/webhook \\
        --i-understand-staging-only

Exit codes:
    0 — helper produced output successfully.
    1 — missing app secret env var (presence check only — value never
        echoed).
    2 — operator passed ``--post-url`` without ``--i-understand-staging-only``.
    3 — POST returned a non-2xx status (only when --post-url is used).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import shlex
import sys
import time
import urllib.error
import urllib.request
from typing import Mapping, Optional

DEFAULT_APP_SECRET_ENV = "V2_STAGING_FB_APP_SECRET"
DEFAULT_USER_AGENT = "TourFireMai-V2-signed-smoke/1.0 (+ops@tourfiremai.com)"
DEFAULT_RECIPIENT_PAGE_ID = "STAGING_PAGE_ID"
DEFAULT_TIMEOUT_S = 10


def build_event(*, psid: str, text: str, mid: Optional[str] = None,
                recipient_page_id: str = DEFAULT_RECIPIENT_PAGE_ID,
                timestamp_ms: Optional[int] = None) -> dict:
    """
    Build a Meta Messenger event in the same shape the V2 webhook expects.

    The shape mirrors ``test_admin_only_runtime_smoke._message_event`` so
    operators see a payload identical to what the runtime smoke tests
    already exercise.
    """
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    if mid is None:
        mid = f"m_signed_smoke_{int(time.time())}"
    return {
        "object": "page",
        "entry": [{
            "messaging": [{
                "sender": {"id": str(psid)},
                "recipient": {"id": str(recipient_page_id)},
                "timestamp": int(timestamp_ms),
                "message": {"mid": str(mid), "text": str(text)},
            }],
        }],
    }


def sign_body(body_bytes: bytes, app_secret: str) -> str:
    """
    Return the ``X-Hub-Signature-256`` value the webhook expects.

    The webhook computes::

        sha256=hmac(app_secret, body_bytes).hexdigest()

    via ``v2.webhook.app._verify_meta_signature``. This helper produces
    the exact same string so the operator can replay an event byte-for-
    byte.
    """
    if not isinstance(body_bytes, (bytes, bytearray)):
        raise TypeError("body_bytes must be bytes")
    if not app_secret:
        raise ValueError("app_secret is required")
    digest = hmac.new(
        app_secret.encode("utf-8"),
        bytes(body_bytes),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def _resolve_app_secret(env: Mapping[str, str], *, env_name: str) -> Optional[str]:
    """Look up the app secret env value without echoing it anywhere."""
    raw = env.get(env_name)
    if raw is None:
        return None
    val = str(raw).strip()
    return val or None


def _redact_signature(sig_header: str) -> str:
    """Return a short, paste-safe rendering of the signature for previews."""
    if not sig_header or not sig_header.startswith("sha256="):
        return "sha256=<missing>"
    hexpart = sig_header.split("=", 1)[1]
    if len(hexpart) <= 8:
        return "sha256=<short>"
    return f"sha256={hexpart[:8]}..."


def build_curl_preview(*, url: str, body_bytes: bytes, signature_header: str,
                       user_agent: str = DEFAULT_USER_AGENT) -> str:
    """
    Compose a single-line curl command operators can copy/paste to replay
    the signed event. The signature value is redacted to a short prefix
    so a screenshot does not leak a long secret-derived token.
    """
    body_text = body_bytes.decode("utf-8")
    redacted_sig = _redact_signature(signature_header)
    return (
        "curl -sS -X POST "
        + shlex.quote(url) + " "
        + "-H " + shlex.quote(f"User-Agent: {user_agent}") + " "
        + "-H 'Content-Type: application/json' "
        + "-H " + shlex.quote(f"X-Hub-Signature-256: {redacted_sig}") + " "
        + "--data " + shlex.quote(body_text)
    )


def _do_post(*, url: str, body_bytes: bytes, signature_header: str,
             user_agent: str = DEFAULT_USER_AGENT,
             timeout_s: int = DEFAULT_TIMEOUT_S) -> tuple[int, str]:
    """
    Perform the actual POST. Returns ``(status_code, response_text)``.

    NEVER raises — instead converts urllib errors into a synthetic
    status code so the caller can decide whether to exit non-zero.
    """
    req = urllib.request.Request(
        url=url,
        data=body_bytes,
        method="POST",
        headers={
            "User-Agent": user_agent,
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature_header,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = resp.read().decode("utf-8", "replace")
            return resp.status, data
    except urllib.error.HTTPError as exc:
        # 4xx / 5xx — surface the status the webhook returned
        try:
            data = exc.read().decode("utf-8", "replace")
        except Exception:  # pragma: no cover — defensive
            data = ""
        return int(exc.code), data
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, f"network_error: {type(exc).__name__}"


def main(argv: Optional[list[str]] = None,
         env: Optional[Mapping[str, str]] = None,
         out=None,
         poster=None) -> int:
    """CLI entry point. Returns the process exit code."""
    env = env if env is not None else os.environ
    out = out if out is not None else sys.stdout
    poster = poster if poster is not None else _do_post

    parser = argparse.ArgumentParser(
        prog="signed_meta_webhook_smoke",
        description=(
            "Offline-safe signed Meta webhook smoke helper. "
            "Prints a redacted curl preview by default. Only POSTs when "
            "--post-url AND --i-understand-staging-only are both passed."
        ),
    )
    parser.add_argument(
        "--psid", required=True,
        help="Sender PSID to embed in the signed event.",
    )
    parser.add_argument(
        "--text", required=True,
        help="Message text to embed in the signed event.",
    )
    parser.add_argument(
        "--mid", default=None,
        help="Optional Messenger mid; defaults to a timestamped value.",
    )
    parser.add_argument(
        "--recipient-page-id", default=DEFAULT_RECIPIENT_PAGE_ID,
        help="Page id put in event.recipient.id (cosmetic; staging only).",
    )
    parser.add_argument(
        "--app-secret-env", default=DEFAULT_APP_SECRET_ENV,
        help=(
            "Env var NAME to read the app secret from. Default "
            f"{DEFAULT_APP_SECRET_ENV}. The value itself is never echoed."
        ),
    )
    parser.add_argument(
        "--post-url", default=None,
        help=(
            "If supplied, POST the signed payload to this URL. "
            "Requires --i-understand-staging-only. NEVER use the "
            "production webhook URL with this helper."
        ),
    )
    parser.add_argument(
        "--i-understand-staging-only", action="store_true",
        help=(
            "Explicit safety opt-in. Required when --post-url is set."
        ),
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT_S,
        help=f"HTTP timeout in seconds (only used with --post-url). Default {DEFAULT_TIMEOUT_S}.",
    )
    args = parser.parse_args(argv)

    secret = _resolve_app_secret(env, env_name=args.app_secret_env)
    if not secret:
        print(
            f"ERROR: env var {args.app_secret_env} is not set or empty. "
            "The helper refuses to sign without a configured secret. "
            "Value is never echoed.",
            file=out,
        )
        return 1

    if args.post_url and not args.i_understand_staging_only:
        print(
            "ERROR: --post-url requires --i-understand-staging-only. "
            "Refusing to POST without the explicit safety opt-in.",
            file=out,
        )
        return 2

    event = build_event(
        psid=args.psid,
        text=args.text,
        mid=args.mid,
        recipient_page_id=args.recipient_page_id,
    )
    body_bytes = json.dumps(event).encode("utf-8")
    sig_header = sign_body(body_bytes, secret)

    target_url = args.post_url or "http://localhost:5000/webhook"
    curl_preview = build_curl_preview(
        url=target_url,
        body_bytes=body_bytes,
        signature_header=sig_header,
    )

    print("Signed Meta webhook smoke (dry-run output by default)", file=out)
    print("=" * 60, file=out)
    print(f"  app_secret_env       : {args.app_secret_env} (configured, value not shown)", file=out)
    print(f"  signature_preview    : {_redact_signature(sig_header)}", file=out)
    print(f"  body_byte_length     : {len(body_bytes)}", file=out)
    print(f"  target_url           : {target_url}", file=out)
    print(f"  mode                 : {'POST' if args.post_url else 'DRY-RUN (no network)'}", file=out)
    print("", file=out)
    print("Curl preview (signature redacted):", file=out)
    print(f"  {curl_preview}", file=out)
    print("", file=out)

    if not args.post_url:
        print(
            "Dry-run only. To actually POST against a local or staging "
            "webhook, re-run with --post-url AND --i-understand-staging-only.",
            file=out,
        )
        return 0

    print(f"POSTing signed payload to {target_url} ...", file=out)
    status, response_text = poster(
        url=target_url,
        body_bytes=body_bytes,
        signature_header=sig_header,
        timeout_s=args.timeout,
    )
    safe_resp = response_text if len(response_text) < 4096 else (response_text[:4096] + "...<truncated>")
    print(f"HTTP status: {status}", file=out)
    print(f"Response   : {safe_resp}", file=out)
    if status < 200 or status >= 300:
        return 3
    return 0


__all__ = [
    "DEFAULT_APP_SECRET_ENV",
    "DEFAULT_RECIPIENT_PAGE_ID",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_USER_AGENT",
    "build_curl_preview",
    "build_event",
    "main",
    "sign_body",
]


if __name__ == "__main__":
    raise SystemExit(main())
