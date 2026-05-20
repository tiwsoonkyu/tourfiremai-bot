"""
v2.tools.refresh_departure_rows — Offline-safe scheduled refresher for the
``tour_departures`` table.

Sprint 5 Package I / DEV-2026-05-20-015.

Why this exists
---------------
The orchestrator uses DB rows by default and only refreshes them when the
freshness gate trips. That keeps the per-customer latency low but leaves
prices stale if no customer touches a tour for long enough. This module
adds a small CLI / callable that scans for tours whose departure rows
are older than a configurable TTL and refreshes them deterministically.

It supports three refresh sources:

    --web-codes ap242455,ap777003     explicit list (highest priority)
    --selected-tours                  active locks in ``selected_tours``
    --stale-only                      every web_code with rows older than TTL

It always supports ``--dry-run``: list what *would* be refreshed without
making any HTTP fetch or DB write.

Hard rules (per CURRENT_DEV_TASK.md)
------------------------------------
- Offline-safe by default. Tests inject an HTTP fake + InMemorySupabase.
- No secrets, no env reads, no live paid providers.
- Refresh failures fail closed: the function returns a per-web_code
  error and never quotes price/availability.
- Bounded: one HTTP fetch per web_code per invocation (no retries).
- Does not delete rows. Idempotent upserts only (delegated to
  ``v2.scraper.detail_enrichment.enrich_tour_detail``).

Public API
----------
    RefreshOutcome                          -- dataclass for one web_code
    RefreshSummary                          -- aggregate dataclass
    refresh_departure_rows(...)             -- main programmatic entry
    collect_stale_web_codes(supabase, ...)  -- audit helper used by the CLI
    collect_selected_tour_web_codes(...)    -- audit helper used by the CLI
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Protocol

logger = logging.getLogger("v2.tools.refresh_departure_rows")

DEFAULT_FRESHNESS_TTL_S = 6 * 60 * 60  # 6h — mirrors orchestrator default.
MAX_BATCH = 200


class HttpClient(Protocol):
    def get(self, url: str, timeout: int = 30) -> Any: ...


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RefreshOutcome:
    web_code: str
    action: str           # 'skipped_dry_run' / 'refreshed' / 'failed' /
                          # 'no_http_client' / 'noop_fresh'
    rows_before: int = 0
    rows_after: int = 0
    error: Optional[str] = None
    refreshed_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "web_code": self.web_code,
            "action": self.action,
            "rows_before": self.rows_before,
            "rows_after": self.rows_after,
            "error": self.error,
            "refreshed_at": self.refreshed_at,
        }


@dataclass
class RefreshSummary:
    requested: int = 0
    refreshed: int = 0
    skipped_dry_run: int = 0
    failed: int = 0
    noop_fresh: int = 0
    no_http_client: int = 0
    outcomes: list[RefreshOutcome] = field(default_factory=list)

    def add(self, outcome: RefreshOutcome) -> None:
        self.outcomes.append(outcome)
        if outcome.action == "refreshed":
            self.refreshed += 1
        elif outcome.action == "skipped_dry_run":
            self.skipped_dry_run += 1
        elif outcome.action == "failed":
            self.failed += 1
        elif outcome.action == "noop_fresh":
            self.noop_fresh += 1
        elif outcome.action == "no_http_client":
            self.no_http_client += 1

    def to_dict(self) -> dict:
        return {
            "requested": self.requested,
            "refreshed": self.refreshed,
            "skipped_dry_run": self.skipped_dry_run,
            "failed": self.failed,
            "noop_fresh": self.noop_fresh,
            "no_http_client": self.no_http_client,
            "outcomes": [o.to_dict() for o in self.outcomes],
        }


# ---------------------------------------------------------------------------
# Audit helpers — discover web_codes that may need refreshing
# ---------------------------------------------------------------------------


def _parse_refreshed_at(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        try:
            ts = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return None


def collect_stale_web_codes(
    supabase: Any, *,
    ttl_s: int = DEFAULT_FRESHNESS_TTL_S,
    now: Optional[datetime] = None,
    limit: int = MAX_BATCH,
) -> list[str]:
    """Return distinct web_codes whose ``tour_departures.refreshed_at`` is
    older than ``ttl_s`` (or NULL — treated as stale on this path).

    Pure read; never writes. Uses ``select_all`` so the InMemory test
    fakes can serve the data without a real query planner.
    """
    now_d = now or datetime.now(timezone.utc)
    threshold = now_d - timedelta(seconds=max(0, int(ttl_s)))
    try:
        rows = supabase.table("tour_departures").select_all({}) or []
    except Exception as e:
        logger.warning("collect_stale_web_codes select_all failed: %s", e)
        return []

    seen: set[str] = set()
    stale: list[str] = []
    for r in rows:
        wc = (r or {}).get("web_code")
        if not wc or wc in seen:
            continue
        ts = _parse_refreshed_at(r.get("refreshed_at"))
        if ts is None or ts < threshold:
            seen.add(wc)
            stale.append(wc)
            if len(stale) >= limit:
                break
    return stale


def collect_selected_tour_web_codes(
    supabase: Any, *, limit: int = MAX_BATCH,
) -> list[str]:
    """Return distinct web_codes referenced by currently-active selected
    tour locks. Used as a "refresh what customers care about" preset."""
    try:
        rows = supabase.table("selected_tours").select_all(
            {"unlocked_at": None}
        ) or []
    except Exception as e:
        logger.warning("collect_selected_tour_web_codes failed: %s", e)
        return []
    seen: set[str] = set()
    out: list[str] = []
    for r in rows:
        tour_id = (r or {}).get("tour_id")
        if not tour_id:
            continue
        try:
            tour = supabase.table("tours_canonical").select_one({"id": tour_id})
        except Exception:
            tour = None
        wc = (tour or {}).get("web_code")
        if not wc or wc in seen:
            continue
        seen.add(wc)
        out.append(wc)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def refresh_departure_rows(
    web_codes: Iterable[str], *,
    supabase: Any,
    http_client: Optional[HttpClient] = None,
    dry_run: bool = False,
    now: Optional[datetime] = None,
    ttl_s: int = DEFAULT_FRESHNESS_TTL_S,
) -> RefreshSummary:
    """Refresh ``tour_departures`` rows for each ``web_code`` in the list.

    For each web_code, we:
      1. Count existing rows (``rows_before``).
      2. If dry_run, record ``skipped_dry_run`` and continue — no HTTP,
         no DB write.
      3. If the existing rows are still fresh (every ``refreshed_at``
         within ``ttl_s``) AND ``force=False``, record ``noop_fresh``
         and continue.
      4. If no http_client is configured, record ``no_http_client``
         and continue — never invent a refresh.
      5. Otherwise call ``enrich_tour_detail(...)``. On parse success,
         record ``refreshed``. On exception or non-parsed result, record
         ``failed`` — the existing rows stay untouched (fail closed).

    Returns a ``RefreshSummary`` with per-web_code outcomes. Never raises.
    """
    summary = RefreshSummary()
    seen: set[str] = set()
    for raw in web_codes:
        wc = (raw or "").lower().strip()
        if not wc or wc in seen:
            continue
        seen.add(wc)
        summary.requested += 1
        summary.add(
            _refresh_one(
                wc, supabase=supabase, http_client=http_client,
                dry_run=dry_run, now=now, ttl_s=ttl_s,
            )
        )
    return summary


def _refresh_one(
    web_code: str, *,
    supabase: Any,
    http_client: Optional[HttpClient],
    dry_run: bool,
    now: Optional[datetime],
    ttl_s: int,
) -> RefreshOutcome:
    try:
        existing = supabase.table("tour_departures").select_all(
            {"web_code": web_code}
        ) or []
    except Exception as e:
        logger.warning("refresh: read existing rows failed wc=%s err=%s",
                       web_code, e)
        existing = []
    rows_before = len(existing)

    if dry_run:
        return RefreshOutcome(
            web_code=web_code, action="skipped_dry_run",
            rows_before=rows_before, rows_after=rows_before,
        )

    # Freshness check — only refresh if at least one row is stale (or no
    # refreshed_at metadata exists at all).
    if existing and not _has_stale_row(existing, ttl_s=ttl_s, now=now):
        return RefreshOutcome(
            web_code=web_code, action="noop_fresh",
            rows_before=rows_before, rows_after=rows_before,
        )

    if http_client is None:
        return RefreshOutcome(
            web_code=web_code, action="no_http_client",
            rows_before=rows_before, rows_after=rows_before,
            error="no_http_client_configured",
        )

    # Look up tour_id from tours_canonical so the upsert can target it.
    tour_id: Optional[str] = None
    try:
        tour_row = supabase.table("tours_canonical").select_one(
            {"web_code": web_code}
        )
        if tour_row:
            tour_id = tour_row.get("id")
    except Exception:
        tour_id = None

    try:
        from v2.scraper.detail_enrichment import enrich_tour_detail
        result = enrich_tour_detail(
            web_code, http=http_client, supabase=supabase,
            tour_id=tour_id, now=now,
        )
    except Exception as e:
        logger.warning("refresh: enrich_tour_detail raised wc=%s err=%s",
                       web_code, e)
        return RefreshOutcome(
            web_code=web_code, action="failed",
            rows_before=rows_before, rows_after=rows_before,
            error=str(e)[:200],
        )

    if not result.parsed:
        return RefreshOutcome(
            web_code=web_code, action="failed",
            rows_before=rows_before, rows_after=rows_before,
            error=result.error or "parse_failed",
        )

    after = supabase.table("tour_departures").select_all(
        {"web_code": web_code}
    ) or []
    return RefreshOutcome(
        web_code=web_code, action="refreshed",
        rows_before=rows_before, rows_after=len(after),
        refreshed_at=(now or datetime.utcnow()).isoformat(),
    )


def _has_stale_row(rows: list, *, ttl_s: int,
                   now: Optional[datetime]) -> bool:
    now_d = now or datetime.now(timezone.utc)
    if ttl_s <= 0:
        return True
    threshold = now_d - timedelta(seconds=int(ttl_s))
    for r in rows:
        ts = _parse_refreshed_at((r or {}).get("refreshed_at"))
        if ts is None:
            # Legacy row without freshness metadata — treat as stale so
            # the refresher backfills it.
            return True
        if ts < threshold:
            return True
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="v2.tools.refresh_departure_rows",
        description=(
            "Refresh tour_departures rows for one or more web_codes. "
            "Defaults to --dry-run so an accidental invocation never "
            "writes to Supabase."
        ),
    )
    p.add_argument(
        "--web-codes", default="",
        help="Comma-separated list of web_codes to refresh.",
    )
    p.add_argument(
        "--selected-tours", action="store_true",
        help="Refresh web_codes referenced by active selected_tours rows.",
    )
    p.add_argument(
        "--stale-only", action="store_true",
        help=(
            "Refresh web_codes whose tour_departures.refreshed_at is older "
            "than --ttl-seconds (or NULL)."
        ),
    )
    p.add_argument(
        "--ttl-seconds", type=int, default=DEFAULT_FRESHNESS_TTL_S,
        help=(
            "Freshness TTL in seconds. Default: 21600 (6h). 0 means "
            "always treat rows as stale."
        ),
    )
    p.add_argument(
        "--limit", type=int, default=MAX_BATCH,
        help=f"Max web_codes to process per run. Default: {MAX_BATCH}.",
    )
    p.add_argument(
        "--no-dry-run", action="store_true",
        help=(
            "Actually perform the HTTP fetch + DB upsert. Default is "
            "dry-run (no network, no DB write)."
        ),
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:  # pragma: no cover - CLI
    """CLI entrypoint. Returns process exit code.

    NB: not exercised by tests directly — tests call
    ``refresh_departure_rows`` and the audit helpers with fakes. The CLI
    intentionally defaults to dry-run so an accidental run never hits
    the network or writes to Supabase.
    """
    args = _build_arg_parser().parse_args(argv)
    # CLI never auto-wires a Supabase or HTTP client — operators must
    # construct those in their own wrapper. This keeps the module
    # importable in environments without psycopg / requests installed.
    print(
        "v2.tools.refresh_departure_rows CLI is offline-safe by default. "
        "Wire `refresh_departure_rows(...)` from your operator script "
        "with explicit supabase + http_client.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
