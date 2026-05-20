from __future__ import annotations

import io
import json
import subprocess
import sys

from v2.tools.admin_only_preflight import (
    build_preflight_report,
    main as preflight_main,
)


SECRET = "SECRET_VALUE_DO_NOT_LEAK_123456789"
PSID = "11112222333344445555"
DASH_TOKEN = "dashboard-token-do-not-leak"
LINE_ID = "U_line_admin_do_not_leak"
REDIS_URL = "redis://secret-user:secret-pass@example.invalid:6379/0"
SUPABASE_URL = "https://mbcihtcdwfofagkxphcu.supabase.co"


def _ready_env() -> dict[str, str]:
    return {
        "V2_ADMIN_ONLY_TEST_MODE": "true",
        "V2_STAGING_ADMIN_TEST_PSID_ALLOW_LIST": PSID,
        "V2_STAGING_DASHBOARD_TOKEN": DASH_TOKEN,
        "V2_STAGING_LINE_ADMIN_ALLOW_LIST": LINE_ID,
        "V2_STAGING_SUPABASE_URL": SUPABASE_URL,
        "V2_STAGING_SUPABASE_SERVICE_ROLE_KEY": SECRET,
        "V2_STAGING_REDIS_URL": REDIS_URL,
        "V2_STAGING_FB_APP_SECRET": SECRET,
        "V2_STAGING_FB_VERIFY_TOKEN": SECRET,
        # These are intentionally optional for this preflight.
        "V2_STAGING_OPENAI_API_KEY": SECRET,
    }


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def test_build_preflight_report_ready_is_secret_safe():
    env = _ready_env()

    report = build_preflight_report(env)
    dumped = _dump(report)

    assert report["ok"] is True
    assert report["status"] == "ok"
    assert report["checks"]["admin_only_test_mode"] == "enabled"
    assert report["checks"]["admin_test_psid_allow_list_count"] == 1
    assert report["checks"]["redis_url"] == "configured"
    assert report["checks"]["supabase_service_role_key"] == "configured"
    assert report["optional_checks"]["openai_api_key"] == "not_required"
    assert SECRET not in dumped
    assert PSID not in dumped
    assert DASH_TOKEN not in dumped
    assert LINE_ID not in dumped
    assert REDIS_URL not in dumped
    assert SUPABASE_URL not in dumped


def test_disabled_admin_only_mode_fails_even_if_other_vars_exist():
    env = _ready_env()
    env["V2_ADMIN_ONLY_TEST_MODE"] = "false"

    report = build_preflight_report(env)

    assert report["ok"] is False
    assert "admin_only_test_mode" in report["missing"]
    assert report["checks"]["admin_only_test_mode"] == "disabled"


def test_missing_allow_list_fails_closed():
    env = _ready_env()
    env.pop("V2_STAGING_ADMIN_TEST_PSID_ALLOW_LIST")

    report = build_preflight_report(env)

    assert report["ok"] is False
    assert "admin_test_psid_allow_list" in report["missing"]
    assert report["checks"]["admin_test_psid_allow_list"] == "missing"


def test_missing_storage_envs_are_reported_without_values():
    env = _ready_env()
    env.pop("V2_STAGING_REDIS_URL")
    env.pop("V2_STAGING_SUPABASE_SERVICE_ROLE_KEY")

    report = build_preflight_report(env)
    dumped = _dump(report)

    assert report["ok"] is False
    assert "redis_url" in report["missing"]
    assert "supabase_service_role_key" in report["missing"]
    assert "REDIS_URL" not in dumped
    assert REDIS_URL not in dumped
    assert SECRET not in dumped


def test_cli_json_ready_returns_zero_and_redacts_values():
    buf = io.StringIO()

    rc = preflight_main(["--json"], env=_ready_env(), out=buf)
    payload = json.loads(buf.getvalue())

    assert rc == 0
    assert payload["ok"] is True
    output = buf.getvalue()
    assert SECRET not in output
    assert PSID not in output
    assert REDIS_URL not in output
    assert SUPABASE_URL not in output


def test_cli_text_missing_returns_one_and_lists_missing_keys():
    env = _ready_env()
    env["V2_ADMIN_ONLY_TEST_MODE"] = "0"
    env.pop("V2_STAGING_REDIS_URL")
    buf = io.StringIO()

    rc = preflight_main([], env=env, out=buf)
    output = buf.getvalue()

    assert rc == 1
    assert "status: missing" in output
    assert "admin_only_test_mode" in output
    assert "redis_url" in output
    assert SECRET not in output
    assert PSID not in output
    assert DASH_TOKEN not in output


def test_module_entrypoint_runs_json_with_empty_env():
    # Regression guard: `python -m v2.tools.admin_only_preflight --json`
    # must be importable and executable even when all env vars are absent.
    result = subprocess.run(
        [sys.executable, "-m", "v2.tools.admin_only_preflight", "--json"],
        text=True,
        capture_output=True,
        env={},
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "admin_only_test_mode" in payload["missing"]
    assert result.stderr == ""


def test_preflight_source_has_no_network_or_paid_provider_imports():
    from pathlib import Path

    src = Path("v2/tools/admin_only_preflight.py").read_text(encoding="utf-8")

    forbidden = [
        "import urllib",
        "import requests",
        "import openai",
        "from openai",
        "import anthropic",
        "from anthropic",
        "import boto3",
        "from boto3",
        "import linebot",
        "from linebot",
        "import supabase",
        "from supabase",
        "import psycopg",
        "from psycopg",
        "import redis",
        "from redis",
    ]
    lowered = src.lower()
    for token in forbidden:
        assert token not in lowered
