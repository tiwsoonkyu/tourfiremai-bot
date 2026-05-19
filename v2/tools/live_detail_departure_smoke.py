#!/usr/bin/env python3
"""
v2.tools.live_detail_departure_smoke — Read-only CLI that fetches a few real
detail pages from tourfiremai.com and prints a redacted summary of what the
deterministic detail-page parser sees.

Sprint 5 Package F (DEV-2026-05-20-012).

Hard rules:
  - READ ONLY. No DB write. No upsert.
  - No LLM, OCR, OpenAI, Anthropic, LINE, Meta, or paid-provider calls.
  - No secrets read. Uses the public HTTP endpoint only.
  - Bounded sample size (3 by default). User-Agent identifies us politely.
  - Output is compact and safe to paste into DEV reports — full HTML is never
    printed; only the parsed structured summary plus a redacted source URL.

Usage:
    python -m v2.tools.live_detail_departure_smoke
    python -m v2.tools.live_detail_departure_smoke ap242455 ap232919

Exit codes:
    0 - parsed all requested codes successfully
    1 - at least one fetch returned non-200 or parse yielded zero rows
    2 - environment problem (e.g. requests not installed)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import Any

DEFAULT_WEB_CODES = ("ap232919", "ap242455", "ap183598")
DEFAULT_USER_AGENT = "TourFireMai-V2-smoke/1.0 (+ops@tourfiremai.com)"
DEFAULT_TIMEOUT = 30


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _configure_stdout() -> None:
    """Make Thai JSON output safe on Windows consoles that default to cp1252."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def _fetch(url: str, *, timeout: int = DEFAULT_TIMEOUT) -> tuple[int, str]:
    try:
        import requests  # local import so the parser module stays stdlib-only
    except ImportError:
        print(
            "[smoke] requests library not installed; cannot fetch live HTML. "
            "Install v2/requirements.txt.",
            file=sys.stderr,
        )
        sys.exit(2)
    resp = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    return resp.status_code, resp.text


def _summary_for_row(row: Any) -> dict[str, Any]:
    """Compact, safe-to-print summary of a single parsed row."""
    return {
        "departure_label_raw": row.departure_label_raw,
        "departure_start": row.departure_start.isoformat() if row.departure_start else None,
        "departure_end": row.departure_end.isoformat() if row.departure_end else None,
        "bus": row.bus,
        "adult_price": row.adult_price,
        "single_supplement_price": row.single_supplement_price,
        "joinland_price": row.joinland_price,
        "group_size": row.group_size,
        "availability_status": row.availability_status,
        "status_text_sample": (row.status_text or "")[:40] or None,
    }


def run(web_codes: list[str]) -> int:
    """Fetch each web_code's detail page, parse it, print a compact summary.

    Returns process-style exit code (0 on full success).
    """
    # Imported lazily so the CLI module file is importable even without the
    # full v2 package on the path (e.g. during unit-test collection).
    from v2.scraper.departure_price_table import (
        BASE_URL,
        DETAIL_PATH,
        parse_departure_price_table,
        parse_detail_header_codes,
    )

    overall_ok = True
    for code in web_codes:
        url = BASE_URL + DETAIL_PATH.format(web_code=code)
        try:
            status, html = _fetch(url)
        except Exception as e:  # noqa: BLE001 (top-level CLI handler)
            print(
                json.dumps(
                    {
                        "web_code": code,
                        "ok": False,
                        "error": f"fetch_failed: {type(e).__name__}",
                        "url": url,
                        "fetched_at": _utc_now_iso(),
                    },
                    ensure_ascii=False,
                )
            )
            overall_ok = False
            continue

        if status != 200:
            print(
                json.dumps(
                    {
                        "web_code": code,
                        "ok": False,
                        "http_status": status,
                        "url": url,
                        "fetched_at": _utc_now_iso(),
                    },
                    ensure_ascii=False,
                )
            )
            overall_ok = False
            continue

        header = parse_detail_header_codes(html)
        rows = parse_departure_price_table(html, code, source_url=url)

        summary = {
            "web_code": code,
            "ok": bool(rows),
            "http_status": status,
            "url": url,
            "fetched_at": _utc_now_iso(),
            "header": header,
            "row_count": len(rows),
            # Cap to 5 rows to keep the report compact.
            "first_rows": [_summary_for_row(r) for r in rows[:5]],
        }
        print(json.dumps(summary, ensure_ascii=False))
        if not rows:
            overall_ok = False

    return 0 if overall_ok else 1


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    parser = argparse.ArgumentParser(
        prog="live_detail_departure_smoke",
        description=(
            "Read-only smoke check for the detail-page departure price table "
            "parser. No DB write. No LLM. No secrets."
        ),
    )
    parser.add_argument(
        "web_codes",
        nargs="*",
        help="One or more web_codes (e.g. ap242455). Defaults to a fixed sample.",
    )
    args = parser.parse_args(argv)
    codes = args.web_codes or list(DEFAULT_WEB_CODES)
    return run(codes)


if __name__ == "__main__":
    raise SystemExit(main())
