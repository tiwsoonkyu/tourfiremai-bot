"""
v2.lib.selected_departure_match — Deterministic helper to match a customer's
date phrase against the parsed departure rows of a *selected* tour.

Sprint 5 Package G (DEV-2026-05-20-013).

Why this exists
---------------
After the customer picks a tour ("ตัวที่ 1", "ap242455", "BCCKG27-HU"), V2
needs to answer date-specific questions like:

    "13 มิ.ย. 3 คน ราคาเท่าไหร่"
    "วันที่ 20 ยังว่างมั้ย"
    "ขอเลทที่ 5 ก.ค. ครับ"

The LLM must NOT be the source of truth for which row matches. This helper
deterministically picks the row (or explicitly returns no-match) from the
list of ``DeparturePriceRow`` objects already parsed by
``v2.scraper.departure_price_table``.

Hard rules (from CURRENT_DEV_TASK.md / V2 Design Rules)
-------------------------------------------------------
- No LLM, no network, no DB. Pure function over already-parsed rows.
- ``web_code``, ``tour_code_real`` and ``airline`` are preserved on the
  returned ``DepartureMatch`` — never collapsed.
- "-" / missing prices stay None on the matched row; this helper does not
  fabricate prices.
- Generic contact-button status text never gets reclassified as sold-out
  here either — the matcher just surfaces whatever
  ``availability_status`` the parser produced.
- When the customer's phrase is ambiguous (multiple rows match) or no row
  matches, the result is an explicit ``no_match`` / ``ambiguous``
  outcome rather than a guess. Callers must handle these states
  deliberately (e.g. ask the customer to clarify, or hand off to staff).

Public API
----------
    DepartureMatch                          -- dataclass for a successful match
    DepartureMatchResult                    -- dataclass for the full outcome
    match_departure(rows, phrase, *, today) -- main entrypoint
    list_available_departures(rows)         -- helper for "what dates do you have"
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from v2.scraper.departure_price_table import (
    DeparturePriceRow,
    THAI_MONTHS,
    parse_thai_date_range,
)

logger = logging.getLogger("v2.selected_departure_match")

__all__ = [
    "DepartureMatch",
    "DepartureMatchResult",
    "list_available_departures",
    "match_departure",
    "parse_customer_date_phrase",
]


# ---------------------------------------------------------------------------
# Match outcome dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DepartureMatch:
    """A single departure row matched against the customer's phrase.

    Mirrors the fields the orchestrator / response_writer would quote back
    to the customer. ``confidence`` is one of ``"high"`` (exact start date
    inside the row), ``"medium"`` (a single-day phrase falls inside a
    multi-day range or matches the start) or ``"low"`` (closest fallback —
    callers should treat as "ask to confirm" rather than as a quote).
    """

    web_code: str
    tour_code_real: Optional[str]
    airline: Optional[str]
    departure_start: Optional[date]
    departure_end: Optional[date]
    departure_label_raw: Optional[str]
    adult_price: Optional[int]
    child_bed_price: Optional[int]
    child_no_bed_price: Optional[int]
    single_supplement_price: Optional[int]
    joinland_price: Optional[int]
    bus: Optional[int]
    group_size: Optional[int]
    status_text: Optional[str]
    availability_status: str
    source_url: Optional[str]
    confidence: str = "high"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("departure_start", "departure_end"):
            v = d.get(k)
            if isinstance(v, date):
                d[k] = v.isoformat()
        return d


@dataclass
class DepartureMatchResult:
    """Top-level result for a date-match attempt.

    Exactly one of ``match`` / ``candidates`` / ``error`` is meaningful at
    a time depending on ``status``:

        - ``"matched"``:       ``match`` is set, ``candidates`` is empty.
        - ``"ambiguous"``:     ``candidates`` contains 2+ rows; ``match`` None.
        - ``"no_match"``:      both empty; ``error`` describes why (e.g.
                               "no rows", "date not in any row",
                               "date in the past").
        - ``"unparseable"``:   we could not parse a date from the phrase.
    """

    status: str
    match: Optional[DepartureMatch] = None
    candidates: list[DepartureMatch] = field(default_factory=list)
    parsed_phrase_date: Optional[date] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "match": self.match.to_dict() if self.match else None,
            "candidates": [c.to_dict() for c in self.candidates],
            "parsed_phrase_date": (
                self.parsed_phrase_date.isoformat()
                if self.parsed_phrase_date
                else None
            ),
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Customer-phrase date parser
# ---------------------------------------------------------------------------

_MONTH_PATTERN = "|".join(re.escape(m) for m in THAI_MONTHS)

_PHRASE_RANGE_RE = re.compile(
    rf"(\d{{1,2}})\s*[-–—~]\s*(\d{{1,2}})\s*({_MONTH_PATTERN})\s*(\d{{2,4}})?"
)
_PHRASE_SINGLE_RE = re.compile(
    rf"(\d{{1,2}})\s*({_MONTH_PATTERN})\s*(\d{{2,4}})?"
)


def parse_customer_date_phrase(
    phrase: str,
    *,
    today: Optional[date] = None,
) -> Optional[date]:
    """Best-effort parse of a Thai customer date phrase to a single date.

    Returns the *start* date when the phrase contains a range — callers use
    that to find the row whose start matches or whose range contains it.

    Returns ``None`` when no clear date can be extracted.
    """
    if not phrase:
        return None
    today_d = today or date.today()
    year_hint = today_d.year

    m = _PHRASE_RANGE_RE.search(phrase)
    if m:
        d1, _d2, mo, yr = m.groups()
        # Reuse the parser's own date-range helper for year resolution so
        # BE / CE / two-digit handling stays in one place.
        start, _end = parse_thai_date_range(
            f"{d1}-{m.group(2)} {mo} {yr or ''}", year_hint=year_hint
        )
        return start

    m = _PHRASE_SINGLE_RE.search(phrase)
    if m:
        d1, mo, yr = m.groups()
        start, _end = parse_thai_date_range(
            f"{d1} {mo} {yr or ''}", year_hint=year_hint
        )
        return start

    return None


# ---------------------------------------------------------------------------
# Match logic
# ---------------------------------------------------------------------------


def _to_match(row: DeparturePriceRow, *, confidence: str) -> DepartureMatch:
    return DepartureMatch(
        web_code=row.web_code,
        tour_code_real=row.tour_code_real,
        airline=row.airline,
        departure_start=row.departure_start,
        departure_end=row.departure_end,
        departure_label_raw=row.departure_label_raw,
        adult_price=row.adult_price,
        child_bed_price=row.child_bed_price,
        child_no_bed_price=row.child_no_bed_price,
        single_supplement_price=row.single_supplement_price,
        joinland_price=row.joinland_price,
        bus=row.bus,
        group_size=row.group_size,
        status_text=row.status_text,
        availability_status=row.availability_status,
        source_url=row.source_url,
        confidence=confidence,
    )


def _row_has_usable_date(row: DeparturePriceRow) -> bool:
    return row.departure_start is not None


def match_departure(
    rows: list[DeparturePriceRow],
    phrase: str,
    *,
    today: Optional[date] = None,
    allow_low_confidence: bool = False,
) -> DepartureMatchResult:
    """Find the departure row matching the customer's phrase.

    Algorithm (cautious, deterministic):

      1. Drop rows without a usable ``departure_start``.
      2. Parse a target date from the phrase. If unparseable → ``unparseable``.
      3. Exact start-date match → ``matched`` with confidence "high".
      4. Date inside a range (start <= target <= end) on exactly one row →
         ``matched`` with confidence "medium".
      5. Date inside a range on multiple rows → ``ambiguous`` (we never
         guess between two valid rows; staff/customer disambiguates).
      6. No row contains the date but a single row starts within ±2 days
         and ``allow_low_confidence`` is True → ``matched`` with
         confidence "low" so the response writer can phrase it as
         "did you mean ..." rather than a hard quote.
      7. Otherwise → ``no_match`` with a descriptive ``error``.
    """
    usable = [r for r in rows if _row_has_usable_date(r)]
    if not usable:
        return DepartureMatchResult(status="no_match", error="no_rows_with_dates")

    target = parse_customer_date_phrase(phrase, today=today)
    if target is None:
        return DepartureMatchResult(status="unparseable", error="no_date_in_phrase")

    # Reject dates in the past — never quote a closed departure.
    today_d = today or date.today()
    if target < today_d:
        return DepartureMatchResult(
            status="no_match",
            parsed_phrase_date=target,
            error="date_in_past",
        )

    # 3. Exact start match
    exact = [r for r in usable if r.departure_start == target]
    if len(exact) == 1:
        return DepartureMatchResult(
            status="matched",
            match=_to_match(exact[0], confidence="high"),
            parsed_phrase_date=target,
        )
    if len(exact) > 1:
        return DepartureMatchResult(
            status="ambiguous",
            candidates=[_to_match(r, confidence="high") for r in exact],
            parsed_phrase_date=target,
            error="multiple_rows_share_start_date",
        )

    # 4-5. In-range matches
    in_range: list[DeparturePriceRow] = []
    for r in usable:
        if r.departure_start is None:
            continue
        end = r.departure_end or r.departure_start
        if r.departure_start <= target <= end:
            in_range.append(r)
    if len(in_range) == 1:
        return DepartureMatchResult(
            status="matched",
            match=_to_match(in_range[0], confidence="medium"),
            parsed_phrase_date=target,
        )
    if len(in_range) > 1:
        return DepartureMatchResult(
            status="ambiguous",
            candidates=[_to_match(r, confidence="medium") for r in in_range],
            parsed_phrase_date=target,
            error="multiple_rows_contain_date",
        )

    # 6. Optional fuzzy fallback
    if allow_low_confidence:
        near: list[tuple[int, DeparturePriceRow]] = []
        for r in usable:
            if r.departure_start is None:
                continue
            delta = abs((r.departure_start - target).days)
            if delta <= 2:
                near.append((delta, r))
        if len(near) == 1:
            return DepartureMatchResult(
                status="matched",
                match=_to_match(near[0][1], confidence="low"),
                parsed_phrase_date=target,
            )
        if len(near) > 1:
            return DepartureMatchResult(
                status="ambiguous",
                candidates=[_to_match(r, confidence="low") for _, r in near],
                parsed_phrase_date=target,
                error="multiple_near_dates",
            )

    return DepartureMatchResult(
        status="no_match",
        parsed_phrase_date=target,
        error="date_not_in_any_row",
    )


# ---------------------------------------------------------------------------
# Listing helper for the orchestrator
# ---------------------------------------------------------------------------


def list_available_departures(
    rows: list[DeparturePriceRow],
    *,
    today: Optional[date] = None,
    limit: Optional[int] = None,
) -> list[DepartureMatch]:
    """Return future, non-sold-out rows for "what dates do you have" prompts.

    Rows without a date are excluded. Sold-out rows are excluded. Rows in
    the past are excluded. Returned in ``departure_start`` order. Each row
    is wrapped in a ``DepartureMatch`` so callers get a uniform shape.
    """
    today_d = today or date.today()
    out: list[DepartureMatch] = []
    for r in rows:
        if not _row_has_usable_date(r):
            continue
        if r.availability_status == "sold_out":
            continue
        if r.departure_start is not None and r.departure_start < today_d:
            continue
        out.append(_to_match(r, confidence="high"))
    out.sort(key=lambda m: m.departure_start or date.max)
    if limit is not None and limit > 0:
        out = out[:limit]
    return out
