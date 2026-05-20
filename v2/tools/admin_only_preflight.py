#!/usr/bin/env python3
"""
Offline admin-only staging readiness check.

This CLI mirrors the safe status view exposed by /admin/runtime-config, then
adds the staging storage env vars needed before an operator performs a real
admin-only Messenger smoke test. It never opens the network, never imports
paid providers, and never prints raw tokens, secrets, URLs, or PSIDs.

Usage:
    python -m v2.tools.admin_only_preflight
    python -m v2.tools.admin_only_preflight --json

Exit codes:
    0 - admin-only staging preflight is ready
    1 - at least one required readiness check is missing or disabled
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Mapping, Optional

from v2.webhook.test_mode_gate import runtime_config_status


_EXTRA_REQUIRED_ENV = {
    "supabase_service_role_key": ("V2_STAGING_SUPABASE_SERVICE_ROLE_KEY",),
    "redis_url": ("V2_STAGING_REDIS_URL",),
}

_OPTIONAL_LIVE_PROVIDER_CHECKS = {
    "openai_api_key": "not_required",
    "openai_model_overrides": "not_required",
    "ocr_provider_key": "not_required",
    "document_parser_key": "not_required",
}

_REQUIRED_CONFIGURED = (
    "admin_test_psid_allow_list",
    "dashboard_admin_token",
    "line_admin_allow_list",
    "supabase_staging_url",
    "fb_app_secret",
    "fb_verify_token",
    "supabase_service_role_key",
    "redis_url",
)


def _presence_status(env: Mapping[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        val = env.get(name)
        if val is not None and str(val).strip():
            return "configured"
    return "missing"


def build_preflight_report(env: Optional[Mapping[str, str]] = None) -> dict:
    """Return a secret-safe admin-only staging readiness report.

    The report contains only status strings and counts. It does not include
    env values, raw PSIDs, tokens, URLs, or signatures.
    """
    env = env if env is not None else os.environ
    base = runtime_config_status(env=env)
    checks = dict(base.get("checks", {}))
    for key, names in _EXTRA_REQUIRED_ENV.items():
        checks[key] = _presence_status(env, names)

    missing = []
    if checks.get("admin_only_test_mode") != "enabled":
        missing.append("admin_only_test_mode")
    for key in _REQUIRED_CONFIGURED:
        if checks.get(key) != "configured":
            missing.append(key)

    report = {
        "ok": not missing,
        "status": "ok" if not missing else "missing",
        "checks": checks,
        "optional_checks": dict(_OPTIONAL_LIVE_PROVIDER_CHECKS),
        "missing": missing,
    }
    return report


def format_text(report: Mapping) -> str:
    """Render a compact, paste-safe text report."""
    lines = [
        "TourFireMai V2 admin-only preflight",
        f"status: {report.get('status')}",
        "",
        "required checks:",
    ]
    checks = report.get("checks") or {}
    for key in sorted(checks):
        lines.append(f"- {key}: {checks[key]}")
    lines.extend(["", "optional live-provider checks:"])
    optional = report.get("optional_checks") or {}
    for key in sorted(optional):
        lines.append(f"- {key}: {optional[key]}")

    missing = list(report.get("missing") or [])
    if missing:
        lines.extend(["", "missing/disabled:"])
        for key in missing:
            lines.append(f"- {key}")
    else:
        lines.extend(["", "ready: admin-only staging checks passed"])
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None, env: Optional[Mapping[str, str]] = None,
         out=None) -> int:
    out = out if out is not None else sys.stdout
    parser = argparse.ArgumentParser(
        prog="admin_only_preflight",
        description=(
            "Offline, secret-safe readiness check for V2 admin-only staging "
            "Messenger smoke tests."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of a text report.",
    )
    args = parser.parse_args(argv)

    report = build_preflight_report(env=env)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), file=out)
    else:
        print(format_text(report), file=out, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
