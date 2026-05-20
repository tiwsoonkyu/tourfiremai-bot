"""
v2.scraper.detail_enrichment — Wire DEV-012 detail-page parser into the V2
scraper / detail-enrichment flow and idempotently persist parsed rows into
``tour_departures`` (migration 021 already applied on staging).

Sprint 5 Package G (DEV-2026-05-20-013).

Why this exists
---------------
The listing scraper (``v2.scraper.scrape_tours``) is enough to maintain the
Top-3 surface, but booking-stage answers need the exact per-departure row
from the *detail* page. DEV-2026-05-20-012 added the deterministic parser
(``v2.scraper.departure_price_table``); this module is the *enrichment*
seam that:

    1. Fetches a single ``/tour/<web_code>`` page via an injected HTTP
       client (no ``requests`` import at the call site — keeps tests
       network-free).
    2. Parses the response with the QA-cleared parser.
    3. Upserts the resulting rows into ``tour_departures`` with an
       idempotent (tour_id or web_code, departure_start, departure_end,
       bus) key.
    4. Returns a small ``DetailEnrichmentResult`` with counts that any
       downstream wiring (selected-tour context, admin view, scheduled
       refresh) can rely on.

Hard rules (from CURRENT_DEV_TASK.md / V2 Design Rules)
-------------------------------------------------------
- Detail-page reads MUST use ``/tour/<web_code>``. The legacy
  ``/intertourdetail/<web_code>`` path 500s on production for the codes
  we care about — this module never builds that URL.
- Detail enrichment is only triggered when a caller needs row-level
  detail (selected tour, admin inspect, "I'm interested in ap242455").
  Generic greetings must not trigger a fetch.
- ``web_code``, ``tour_code_real``, and ``airline`` are kept distinct
  fields end-to-end; no field is collapsed or coalesced into another.
- ``-`` cells stay NULL in the persistence payload. No 0 substitution.
- The contact-button copy ("ติดต่อเจ้าหน้าที่") is NEVER interpreted as
  sold-out — that signal still lives in ``tour_availability_overrides``.
- LLM is never the source of truth here. This module never invokes any
  LLM, OCR, or paid provider.

Public API
----------
    build_detail_url(web_code)                 -> str
    fetch_detail_html(web_code, http=None, ...)  -> str | None
    enrich_tour_detail(web_code, *, http, supabase, tour_id=None, ...)
                                               -> DetailEnrichmentResult
    upsert_departure_rows(rows, *, supabase, tour_id=None)
                                               -> DetailPersistenceResult
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol

from .departure_price_table import (
    BASE_URL,
    DETAIL_PATH,
    DeparturePriceRow,
    idempotency_key,
    parse_departure_price_table,
    parse_detail_header_codes,
    to_tour_departure_rows,
)

logger = logging.getLogger("v2.scraper.detail_enrichment")

# Re-export so callers can `from v2.scraper.detail_enrichment import BASE_URL`.
__all__ = [
    "BASE_URL",
    "DETAIL_PATH",
    "DetailEnrichmentResult",
    "DetailPersistenceResult",
    "HttpClient",
    "build_detail_url",
    "enrich_tour_detail",
    "fetch_detail_html",
    "upsert_departure_rows",
]


DEFAULT_TIMEOUT = 30


class HttpClient(Protocol):
    """Minimal HTTP duck type matching v2.scraper.scrape_tours.HttpClient."""

    def get(self, url: str, timeout: int = DEFAULT_TIMEOUT) -> Any: ...


# ---------------------------------------------------------------------------
# URL helpers — detail reads MUST use /tour/<web_code>
# ---------------------------------------------------------------------------


def build_detail_url(web_code: str) -> str:
    """Return the canonical detail-page URL for a web_code.

    Detail reads MUST use ``/tour/<web_code>``. The legacy
    ``/intertourdetail/`` path 500s on production.
    """
    if not web_code:
        raise ValueError("web_code required")
    return BASE_URL + DETAIL_PATH.format(web_code=web_code.lower())


def fetch_detail_html(
    web_code: str,
    *,
    http: HttpClient,
    timeout: int = DEFAULT_TIMEOUT,
) -> Optional[str]:
    """Fetch the detail page HTML for a single web_code.

    Returns ``None`` when the page is unreachable or non-200, so the caller
    can fall back to listing-page data without raising. No retries, no
    backoff — schedule-level concerns belong to a scheduler, not here.
    """
    url = build_detail_url(web_code)
    try:
        resp = http.get(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — surface as non-fatal miss
        logger.warning("detail_fetch_failed web_code=%s err=%s", web_code, exc)
        return None
    status = getattr(resp, "status_code", None)
    if status != 200:
        logger.warning("detail_fetch_non200 web_code=%s status=%s", web_code, status)
        return None
    text = getattr(resp, "text", "") or ""
    if not text:
        logger.warning("detail_fetch_empty_body web_code=%s", web_code)
        return None
    return text


# ---------------------------------------------------------------------------
# Persistence helper — idempotent upsert into tour_departures
# ---------------------------------------------------------------------------


@dataclass
class DetailPersistenceResult:
    """Outcome of upserting parsed detail rows into ``tour_departures``."""

    upserted: int = 0
    inserted: int = 0
    updated: int = 0
    skipped_no_date: int = 0
    errors: list[str] = field(default_factory=list)
    idempotency_keys: list[tuple[Any, ...]] = field(default_factory=list)


def upsert_departure_rows(
    rows: list[DeparturePriceRow],
    *,
    supabase: Any,
    tour_id: Optional[str] = None,
) -> DetailPersistenceResult:
    """Idempotently upsert parsed detail rows into ``tour_departures``.

    Uses the (tour_id or web_code, departure_start, departure_end, bus)
    idempotency key. Rows without a ``departure_start`` are skipped, never
    inserted as NULL-date rows.

    Hard rules enforced here, not in the parser:
        - Never coerce a missing price to 0. Missing prices stay NULL.
        - Never destroy existing rows. The upsert is additive.
        - Legacy mirror columns (``departure_date`` / ``return_date`` /
          ``price``) are also written so pre-021 read paths keep working.
    """
    if not rows:
        return DetailPersistenceResult()

    payloads = to_tour_departure_rows(rows, tour_id=tour_id)
    skipped = len(rows) - len(payloads)

    result = DetailPersistenceResult(skipped_no_date=skipped)
    table = supabase.table("tour_departures")

    for payload in payloads:
        key = idempotency_key(payload, tour_id=tour_id)
        result.idempotency_keys.append(key)

        match: dict[str, Any] = {
            "departure_start": payload["departure_start"],
            "departure_end": payload["departure_end"],
            "bus": payload["bus"],
        }
        if tour_id is not None:
            match["tour_id"] = tour_id
        else:
            match["web_code"] = payload["web_code"]

        try:
            existed_before = bool(table.select_one(match))
            table.upsert(match=match, insert=payload, update=payload)
            result.upserted += 1
            if existed_before:
                result.updated += 1
            else:
                result.inserted += 1
        except Exception as exc:  # noqa: BLE001 — never crash the caller
            logger.warning(
                "detail_upsert_failed web_code=%s start=%s err=%s",
                payload.get("web_code"),
                payload.get("departure_start"),
                exc,
            )
            result.errors.append(
                f"{payload.get('web_code')}@{payload.get('departure_start')}: {exc}"
            )

    return result


# ---------------------------------------------------------------------------
# Top-level enrichment: fetch + parse + (optional) persist
# ---------------------------------------------------------------------------


@dataclass
class DetailEnrichmentResult:
    """Combined fetch + parse + persist outcome for one detail enrichment."""

    web_code: str
    source_url: str
    fetched: bool = False
    parsed: bool = False
    persisted: bool = False
    rows: list[DeparturePriceRow] = field(default_factory=list)
    header: dict[str, Optional[str]] = field(default_factory=dict)
    persistence: Optional[DetailPersistenceResult] = None
    fetched_at: Optional[datetime] = None
    error: Optional[str] = None

    def to_summary(self) -> dict[str, Any]:
        """Compact dict for admin / log surfaces (no wholesale, no PSID)."""
        return {
            "web_code": self.web_code,
            "source_url": self.source_url,
            "fetched": self.fetched,
            "parsed": self.parsed,
            "persisted": self.persisted,
            "row_count": len(self.rows),
            "header": dict(self.header),
            "upserted": (self.persistence.upserted if self.persistence else 0),
            "inserted": (self.persistence.inserted if self.persistence else 0),
            "updated": (self.persistence.updated if self.persistence else 0),
            "skipped_no_date": (
                self.persistence.skipped_no_date if self.persistence else 0
            ),
            "errors": list(self.persistence.errors) if self.persistence else [],
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "error": self.error,
        }


def enrich_tour_detail(
    web_code: str,
    *,
    http: HttpClient,
    supabase: Optional[Any] = None,
    tour_id: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    now: Optional[datetime] = None,
    persist: bool = True,
) -> DetailEnrichmentResult:
    """Fetch a detail page, parse it, and (optionally) persist rows.

    Returns a ``DetailEnrichmentResult`` regardless of failure mode so the
    caller never needs to ``try/except`` the orchestration boundary.

    Args:
        web_code: e.g. ``"ap242455"``. Lower-cased internally.
        http: An injected HTTP client (``requests``-compat ``.get(url, timeout)``).
              Tests pass a fake.
        supabase: Optional supabase-like client. If ``None`` (or ``persist=False``),
                  the parser still runs but no DB writes happen.
        tour_id: Preferred persistence key. When ``None``, persistence falls
                 back to ``web_code`` matching.
        timeout: HTTP timeout in seconds.
        now: Optional UTC timestamp injection (tests pin this).
        persist: Set to ``False`` to skip the DB upsert entirely.
    """
    norm = (web_code or "").lower()
    source_url = build_detail_url(norm) if norm else ""
    fetched_at = now or datetime.utcnow()

    result = DetailEnrichmentResult(
        web_code=norm,
        source_url=source_url,
        fetched_at=fetched_at,
    )
    if not norm:
        result.error = "missing_web_code"
        return result

    html = fetch_detail_html(norm, http=http, timeout=timeout)
    if html is None:
        result.error = "fetch_failed_or_non200"
        return result
    result.fetched = True

    try:
        result.header = parse_detail_header_codes(html)
        result.rows = parse_departure_price_table(html, norm, source_url=source_url)
        result.parsed = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("detail_parse_failed web_code=%s err=%s", norm, exc)
        result.error = f"parse_failed: {exc}"
        return result

    if persist and supabase is not None and result.rows:
        result.persistence = upsert_departure_rows(
            result.rows, supabase=supabase, tour_id=tour_id
        )
        result.persisted = bool(result.persistence and result.persistence.upserted)

    return result
