"""
Tests for v2.tools.signed_meta_webhook_smoke — DEV-2026-05-20-016.

These tests are pure-function and CLI smoke tests. They never reach Meta,
LINE, OpenAI, OCR providers, Supabase, Redis, or any network. The single
"poster" callable is injected by every test that exercises the POST path.

Hard rules enforced here:
    - The helper never POSTs by default (dry-run is the only no-flag path).
    - The helper refuses to POST unless --post-url AND
      --i-understand-staging-only are both passed.
    - The helper refuses to sign without a configured app-secret env var
      and reports the env var NAME (never the value).
    - The signature header conforms to the same format the V2 webhook
      verifies (sha256=<lowercase hex>).
    - The signature value is redacted in CLI previews and curl strings.
    - The same body + secret hashed by ``sign_body`` is accepted by the
      same verifier the real webhook uses.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json

import pytest

from v2.tools.signed_meta_webhook_smoke import (
    DEFAULT_APP_SECRET_ENV,
    build_curl_preview,
    build_event,
    main as smoke_main,
    sign_body,
)


SECRET_FIXTURE = "TEST_APP_SECRET_DO_NOT_LEAK_xyz1234567890"
PSID_FIXTURE = "11112222333344445555"


# ---------------------------------------------------------------------------
# Pure-function sign_body / build_event
# ---------------------------------------------------------------------------


def test_sign_body_matches_meta_signature_format():
    body = b'{"object":"page","entry":[]}'
    sig = sign_body(body, SECRET_FIXTURE)
    assert sig.startswith("sha256=")
    hexpart = sig.split("=", 1)[1]
    # Lowercase hex, 64 chars for SHA-256
    assert len(hexpart) == 64
    assert all(c in "0123456789abcdef" for c in hexpart)


def test_sign_body_is_compatible_with_webhook_verifier():
    """
    The helper's signature must be accepted by the V2 webhook verifier
    ``_verify_meta_signature``. This pins the wire-format contract.
    """
    from v2.webhook.app import _verify_meta_signature

    event = build_event(psid=PSID_FIXTURE, text="hello", mid="m_test",
                        timestamp_ms=1_700_000_000_000)
    body = json.dumps(event).encode("utf-8")
    sig = sign_body(body, SECRET_FIXTURE)
    assert _verify_meta_signature(body, sig, SECRET_FIXTURE) is True


def test_sign_body_rejects_empty_secret():
    with pytest.raises(ValueError):
        sign_body(b"x", "")


def test_sign_body_rejects_non_bytes():
    with pytest.raises(TypeError):
        sign_body("not bytes", SECRET_FIXTURE)  # type: ignore[arg-type]


def test_build_event_shape_matches_runtime_smoke():
    event = build_event(psid=PSID_FIXTURE, text="hi", mid="m_abc",
                        timestamp_ms=42)
    assert event["object"] == "page"
    msg = event["entry"][0]["messaging"][0]
    assert msg["sender"]["id"] == PSID_FIXTURE
    assert msg["message"]["mid"] == "m_abc"
    assert msg["message"]["text"] == "hi"
    assert msg["timestamp"] == 42


# ---------------------------------------------------------------------------
# Curl preview redacts the signature.
# ---------------------------------------------------------------------------


def test_curl_preview_redacts_signature_to_first_8_hex_chars():
    body = b'{"x":1}'
    full_sig = sign_body(body, SECRET_FIXTURE)
    preview = build_curl_preview(
        url="http://localhost:5000/webhook",
        body_bytes=body,
        signature_header=full_sig,
    )
    full_hex = full_sig.split("=", 1)[1]
    # Full digest must never appear in the curl preview.
    assert full_hex not in preview
    # First 8 chars must appear (redacted prefix is intentional).
    assert full_hex[:8] in preview
    # Secret must never leak via the preview.
    assert SECRET_FIXTURE not in preview


def test_curl_preview_uses_user_agent_and_content_type():
    body = b'{"x":1}'
    sig = sign_body(body, SECRET_FIXTURE)
    preview = build_curl_preview(
        url="http://localhost:5000/webhook",
        body_bytes=body,
        signature_header=sig,
    )
    assert "User-Agent" in preview
    assert "Content-Type: application/json" in preview
    assert "X-Hub-Signature-256" in preview


# ---------------------------------------------------------------------------
# CLI: dry-run is default; explicit flags required to POST.
# ---------------------------------------------------------------------------


def _noop_poster(**kwargs):
    raise AssertionError(
        "poster was invoked when dry-run was expected — POST safety broken!"
    )


def test_main_dry_run_default_does_not_post(monkeypatch):
    buf = io.StringIO()
    env = {DEFAULT_APP_SECRET_ENV: SECRET_FIXTURE}
    rc = smoke_main(
        argv=[
            "--psid", PSID_FIXTURE,
            "--text", "dry run",
        ],
        env=env,
        out=buf,
        poster=_noop_poster,
    )
    assert rc == 0
    output = buf.getvalue()
    assert "DRY-RUN" in output
    assert "POST" not in output.split("mode")[1].split("\n")[0] or "DRY-RUN" in output
    # Secret value must NEVER appear in output.
    assert SECRET_FIXTURE not in output


def test_main_missing_secret_returns_1_and_names_the_env_var():
    buf = io.StringIO()
    rc = smoke_main(
        argv=[
            "--psid", PSID_FIXTURE,
            "--text", "no secret",
        ],
        env={},  # explicitly empty
        out=buf,
        poster=_noop_poster,
    )
    assert rc == 1
    output = buf.getvalue()
    assert DEFAULT_APP_SECRET_ENV in output
    # No accidental secret leak in error path.
    assert SECRET_FIXTURE not in output


def test_main_post_url_without_safety_opt_in_returns_2():
    buf = io.StringIO()
    env = {DEFAULT_APP_SECRET_ENV: SECRET_FIXTURE}
    rc = smoke_main(
        argv=[
            "--psid", PSID_FIXTURE,
            "--text", "would post",
            "--post-url", "http://localhost:5000/webhook",
        ],
        env=env,
        out=buf,
        poster=_noop_poster,
    )
    assert rc == 2
    output = buf.getvalue()
    assert "--i-understand-staging-only" in output


def test_main_post_url_with_opt_in_invokes_injected_poster():
    captured = {}

    def fake_poster(*, url, body_bytes, signature_header, timeout_s):
        captured["url"] = url
        captured["body_bytes"] = body_bytes
        captured["sig"] = signature_header
        captured["timeout"] = timeout_s
        return 200, '{"status":"accepted","scheduled":1,"filtered":0}'

    buf = io.StringIO()
    env = {DEFAULT_APP_SECRET_ENV: SECRET_FIXTURE}
    rc = smoke_main(
        argv=[
            "--psid", PSID_FIXTURE,
            "--text", "real post",
            "--post-url", "http://localhost:5000/webhook",
            "--i-understand-staging-only",
        ],
        env=env,
        out=buf,
        poster=fake_poster,
    )
    assert rc == 0
    assert captured["url"] == "http://localhost:5000/webhook"
    assert captured["sig"].startswith("sha256=")
    # The webhook verifier must accept the signature the helper produced.
    from v2.webhook.app import _verify_meta_signature
    assert _verify_meta_signature(captured["body_bytes"], captured["sig"], SECRET_FIXTURE) is True
    # Body must be a valid Meta page event with the operator's PSID & text.
    body = json.loads(captured["body_bytes"].decode("utf-8"))
    msg = body["entry"][0]["messaging"][0]
    assert msg["sender"]["id"] == PSID_FIXTURE
    assert msg["message"]["text"] == "real post"


def test_main_post_with_non_2xx_returns_3():
    def err_poster(**kwargs):
        return 401, "invalid signature"

    buf = io.StringIO()
    env = {DEFAULT_APP_SECRET_ENV: SECRET_FIXTURE}
    rc = smoke_main(
        argv=[
            "--psid", PSID_FIXTURE,
            "--text", "will 401",
            "--post-url", "http://localhost:5000/webhook",
            "--i-understand-staging-only",
        ],
        env=env,
        out=buf,
        poster=err_poster,
    )
    assert rc == 3


def test_main_supports_custom_app_secret_env_name():
    """An operator can point the helper at a different env var name."""
    buf = io.StringIO()
    env = {"V2_STAGING_FB_APP_SECRET_ALT": SECRET_FIXTURE}
    rc = smoke_main(
        argv=[
            "--psid", PSID_FIXTURE,
            "--text", "alt env",
            "--app-secret-env", "V2_STAGING_FB_APP_SECRET_ALT",
        ],
        env=env,
        out=buf,
        poster=_noop_poster,
    )
    assert rc == 0
    output = buf.getvalue()
    assert "V2_STAGING_FB_APP_SECRET_ALT" in output
    assert SECRET_FIXTURE not in output


def test_main_dry_run_output_never_contains_full_signature():
    """Even on the happy dry-run path, the full sha256 digest is redacted."""
    buf = io.StringIO()
    env = {DEFAULT_APP_SECRET_ENV: SECRET_FIXTURE}
    rc = smoke_main(
        argv=[
            "--psid", PSID_FIXTURE,
            "--text", "redaction smoke",
        ],
        env=env,
        out=buf,
        poster=_noop_poster,
    )
    assert rc == 0
    output = buf.getvalue()
    # Recompute the full signature locally to ensure it doesn't appear.
    event = build_event(psid=PSID_FIXTURE, text="redaction smoke")
    # mid + timestamp differ each run; signature differs too. Instead
    # assert there is no 64-char hex run anywhere in the output.
    import re
    matches = re.findall(r"[0-9a-f]{64}", output)
    assert matches == []


# ---------------------------------------------------------------------------
# Negative test: helper has no live-provider import surface.
# ---------------------------------------------------------------------------


def test_helper_has_no_paid_provider_imports():
    """
    Smoke check: the helper module must not import live Meta / OpenAI /
    LINE / Supabase / Redis SDKs. The helper is HTTP + HMAC only.
    """
    from v2.tools import signed_meta_webhook_smoke as mod

    source = open(mod.__file__, "r", encoding="utf-8").read()
    forbidden = (
        "from openai",
        "import openai",
        "from anthropic",
        "import anthropic",
        "from linebot",
        "import linebot",
        "from supabase",
        "import supabase",
        "import psycopg",
        "from psycopg",
        "import redis",
        "from redis",
        "requests.post",   # default stack only — no `requests` dep at all
        "requests.get",
    )
    for needle in forbidden:
        assert needle not in source, f"Helper must not import {needle!r}"
