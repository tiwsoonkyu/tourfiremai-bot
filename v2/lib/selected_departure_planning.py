"""
v2.lib.selected_departure_planning — Compose a compact, LLM-safe selected
departure planning bundle for the response writer.

Sprint 5 Package H (DEV-2026-05-20-014).

Why this exists
---------------
DEV-2026-05-20-013 wired the detail-page departure parser into the V2
scraper / detail enrichment and selected-tour memory. This module is the
*planning* seam that turns those deterministic outputs into the small dict
the response writer needs:

    - which selected tour (web_code, tour_code_real, airline, name) we
      will quote — kept strictly separate, never collapsed;
    - whether the customer's date phrase matches a row (high / medium /
      low / ambiguous / no_match / unparseable / no_phrase);
    - the matched row's compact data (only when confidence is "high");
    - a list of available departures the LLM can offer when the customer
      hasn't picked a date yet, when the phrase is ambiguous, or when the
      phrase parses but matches no row;
    - a deterministic Thai natural-language ``safe_planning_note`` that
      tells the LLM how to phrase the reply without quoting prices /
      seats as final or guessing.

Hard rules (from CURRENT_DEV_TASK.md / V2 Design Rules)
-------------------------------------------------------
- LLM is never the source of truth — this module never calls an LLM.
- No network, no DB, no OCR, no paid providers.
- web_code, tour_code_real, and airline are preserved on every output
  field — never merged or coalesced.
- "-" / missing values stay None on the output row (never 0).
- A contact-button row stays ``availability_status="unknown"`` — never
  reclassified as sold_out by this module. Final sold-out / full
  blocking is owned by ``v2.lib.page_post_context`` and remains the
  source of truth.
- The bot must never confirm seat availability or quote a final price.
  ``safe_planning_note`` always carries that constraint.

Public API
----------
    SelectedDeparturePlanning           -- dataclass for the bundle
    build_selected_departure_planning   -- main entrypoint
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from v2.lib.selected_departure_match import (
    DepartureMatch,
    DepartureMatchResult,
    list_available_departures,
    match_departure,
    parse_customer_date_phrase,
)
from v2.scraper.departure_price_table import DeparturePriceRow

logger = logging.getLogger("v2.selected_departure_planning")

__all__ = [
    "SelectedDeparturePlanning",
    "build_selected_departure_planning",
    "compact_departure_dict",
    "row_dict_to_departure_price_row",
]


# Match-status constants surfaced on the planning bundle.
MATCH_STATUS_HIGH = "matched_high"
MATCH_STATUS_MEDIUM = "matched_medium"
MATCH_STATUS_LOW = "matched_low"
MATCH_STATUS_AMBIGUOUS = "ambiguous"
MATCH_STATUS_NO_MATCH = "no_match"
MATCH_STATUS_UNPARSEABLE = "unparseable"
MATCH_STATUS_NO_PHRASE = "no_phrase"
MATCH_STATUS_NO_ROWS = "no_rows"


# Deterministic Thai planning notes. Kept short — the LLM uses them as
# guidance, NOT verbatim. They never quote prices or seats.
_NOTE_HIGH_MATCH = (
    "พบรอบเดินทางตรงกับวันที่ลูกค้าระบุ ใช้ข้อมูลของแถวที่ matched เท่านั้น "
    "ห้ามเดาราคา/ที่นั่ง ห้ามยืนยันว่ายังว่าง 100% "
    "(ต้องให้ทีมงานเช็กที่นั่งและยืนยันอีกครั้ง)."
)
_NOTE_HIGH_MATCH_SOLD_OUT = (
    "รอบเดินทางที่ตรงกับวันที่นี้ระบบแสดงสถานะ sold_out — "
    "แจ้งลูกค้าว่ารอบนี้เต็มและเสนอตัวเลือกวันเดินทางอื่นจากรายการที่เหลือ."
)
_NOTE_AMBIGUOUS = (
    "วันที่ที่ลูกค้าระบุตรงกับรอบเดินทางมากกว่าหนึ่งรอบ — "
    "ห้ามเดา ขอให้ลูกค้ายืนยันรอบที่ต้องการก่อนเสนอราคา."
)
_NOTE_MEDIUM_LOW = (
    "วันที่ที่ลูกค้าระบุยังไม่แน่ใจว่าตรงกับรอบไหน — "
    "ทวนวันที่กับลูกค้าและขอให้ยืนยันรอบก่อนเสนอราคาเป็นทางการ."
)
_NOTE_NO_MATCH = (
    "วันที่ที่ลูกค้าระบุไม่ตรงกับรอบเดินทางใด ๆ ของทัวร์นี้ — "
    "เสนอตัวเลือกรอบเดินทางในตาราง available_departures ให้ลูกค้าเลือก."
)
_NOTE_PAST_DATE = (
    "วันที่ที่ลูกค้าระบุผ่านมาแล้ว — แจ้งลูกค้าและเสนอรอบเดินทางในอนาคต."
)
_NOTE_NO_ROWS = (
    "ทัวร์นี้ยังไม่มีรอบเดินทางในระบบ — ส่งต่อให้ทีมงานเช็กข้อมูลให้ลูกค้า."
)
_NOTE_UNPARSEABLE = (
    "ลูกค้ายังไม่ได้ระบุวันที่ที่ชัดเจน — ขอให้ลูกค้าระบุวันที่หรือเลือกรอบ "
    "จากรายการ available_departures ก่อนเสนอราคา."
)
_NOTE_NO_PHRASE_HAS_DATES = (
    "ลูกค้ายังไม่ได้ระบุวันที่เดินทาง — แสดงตัวเลือกรอบเดินทางที่เปิดรับ "
    "ใน available_departures และให้ลูกค้าเลือก ห้ามยืนยันราคาเป็นทางการ."
)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class SelectedDeparturePlanning:
    """Compact, LLM-safe planning bundle for the response writer.

    Empty / None values are stripped by ``to_compact_dict`` so the LLM
    payload never contains noise. ``ask_confirmation`` is always
    explicit (True / False), never omitted.
    """

    web_code: str
    tour_code_real: Optional[str] = None
    airline: Optional[str] = None
    tour_name: Optional[str] = None

    match_status: str = MATCH_STATUS_NO_PHRASE
    confidence: Optional[str] = None
    parsed_phrase_date: Optional[str] = None

    matched_departure: Optional[dict] = None
    ambiguous_candidates: list[dict] = field(default_factory=list)
    available_departures: list[dict] = field(default_factory=list)

    ask_confirmation: bool = False
    matched_row_availability: Optional[str] = None

    safe_planning_note: Optional[str] = None

    def to_compact_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "web_code": self.web_code,
            "tour_code_real": self.tour_code_real,
            "airline": self.airline,
            "tour_name": self.tour_name,
            "match_status": self.match_status,
            "confidence": self.confidence,
            "parsed_phrase_date": self.parsed_phrase_date,
            "matched_departure": self.matched_departure,
            "ambiguous_candidates": self.ambiguous_candidates,
            "available_departures": self.available_departures,
            "ask_confirmation": self.ask_confirmation,
            "matched_row_availability": self.matched_row_availability,
            "safe_planning_note": self.safe_planning_note,
        }
        # Drop empty / None values to keep the LLM payload compact.
        # ``ask_confirmation`` is always kept because False is meaningful.
        compact: dict[str, Any] = {}
        for k, v in out.items():
            if k == "ask_confirmation":
                compact[k] = bool(v)
                continue
            if k == "match_status":
                compact[k] = v
                continue
            if v in (None, "", [], {}):
                continue
            compact[k] = v
        return compact


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def compact_departure_dict(d: DepartureMatch) -> dict[str, Any]:
    """Return a minimal dict for one matched row.

    Only the fields the bot may quote are included. Wholesale / PSID /
    internal raw_cells are intentionally excluded.
    """
    def _iso(v: Any) -> Optional[str]:
        if v is None:
            return None
        try:
            return v.isoformat()
        except AttributeError:  # already str / None
            return v if isinstance(v, str) else None

    return {
        "web_code": d.web_code,
        "tour_code_real": d.tour_code_real,
        "airline": d.airline,
        "departure_start": _iso(d.departure_start),
        "departure_end": _iso(d.departure_end),
        "departure_label_raw": d.departure_label_raw,
        "adult_price": d.adult_price,
        "child_bed_price": d.child_bed_price,
        "child_no_bed_price": d.child_no_bed_price,
        "single_supplement_price": d.single_supplement_price,
        "joinland_price": d.joinland_price,
        "bus": d.bus,
        "group_size": d.group_size,
        "status_text": d.status_text,
        "availability_status": d.availability_status,
    }


def row_dict_to_departure_price_row(
    d: dict, *, default_web_code: str = ""
) -> DeparturePriceRow:
    """Convert a persisted ``tour_departures`` dict row back into a
    ``DeparturePriceRow`` so the deterministic matcher can consume it.

    Handles ISO date strings and ``None`` fields. Never coerces a missing
    price to 0.
    """
    def _to_date(v: Any) -> Optional[date]:
        if v is None or v == "":
            return None
        if isinstance(v, date):
            return v
        if isinstance(v, str):
            try:
                return date.fromisoformat(v)
            except ValueError:
                return None
        return None

    raw_cells = d.get("raw_cells") or []
    if not isinstance(raw_cells, list):
        raw_cells = []

    return DeparturePriceRow(
        web_code=d.get("web_code") or default_web_code,
        tour_code_real=d.get("tour_code_real"),
        departure_start=_to_date(d.get("departure_start")),
        departure_end=_to_date(d.get("departure_end")),
        departure_label_raw=d.get("departure_label_raw"),
        bus=d.get("bus"),
        adult_price=d.get("adult_price"),
        child_bed_price=d.get("child_bed_price"),
        child_no_bed_price=d.get("child_no_bed_price"),
        single_supplement_price=d.get("single_supplement_price"),
        joinland_price=d.get("joinland_price"),
        group_size=d.get("group_size"),
        status_text=d.get("status_text"),
        status_class=d.get("status_class"),
        availability_status=d.get("availability_status") or "unknown",
        source_url=d.get("source_url"),
        airline=d.get("airline"),
        raw_cells=list(raw_cells),
    )


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def build_selected_departure_planning(
    *,
    rows: list[DeparturePriceRow],
    customer_text: str,
    selected_tour: dict,
    today: Optional[date] = None,
    available_limit: int = 6,
) -> SelectedDeparturePlanning:
    """Build the planning bundle for the response writer.

    Args:
        rows: parsed ``DeparturePriceRow`` list for the selected tour.
        customer_text: raw inbound text from the customer.
        selected_tour: dict with keys ``web_code``, ``tour_code_real``,
            ``airline``, ``name``/``tour_name``. Only these four fields
            are used; any extras are ignored.
        today: optional reference date for past-date rejection (tests
            pin this).
        available_limit: cap on ``available_departures`` to keep LLM
            payloads small.
    """
    web_code = (selected_tour or {}).get("web_code") or ""
    tour_code_real = (selected_tour or {}).get("tour_code_real")
    airline = (selected_tour or {}).get("airline")
    tour_name = (
        (selected_tour or {}).get("tour_name")
        or (selected_tour or {}).get("name")
    )

    # Pre-compute available departures once — used in nearly every branch.
    available = list_available_departures(rows, today=today, limit=available_limit)
    available_dicts = [compact_departure_dict(m) for m in available]

    # No rows at all → cannot quote anything from this tour.
    if not rows:
        return SelectedDeparturePlanning(
            web_code=web_code,
            tour_code_real=tour_code_real,
            airline=airline,
            tour_name=tour_name,
            match_status=MATCH_STATUS_NO_ROWS,
            available_departures=[],
            safe_planning_note=_NOTE_NO_ROWS,
        )

    # No date phrase → just expose the tour + available dates.
    parsed = parse_customer_date_phrase(customer_text or "", today=today)
    if parsed is None:
        note = (
            _NOTE_NO_PHRASE_HAS_DATES
            if available_dicts
            else _NOTE_NO_ROWS
        )
        return SelectedDeparturePlanning(
            web_code=web_code,
            tour_code_real=tour_code_real,
            airline=airline,
            tour_name=tour_name,
            match_status=MATCH_STATUS_NO_PHRASE,
            available_departures=available_dicts,
            safe_planning_note=note,
        )

    # Run the deterministic matcher.
    result: DepartureMatchResult = match_departure(
        rows, customer_text or "", today=today,
    )

    parsed_iso = (
        result.parsed_phrase_date.isoformat()
        if result.parsed_phrase_date
        else None
    )

    if result.status == "matched" and result.match is not None:
        conf = result.match.confidence  # 'high' / 'medium' / 'low'
        match_dict = compact_departure_dict(result.match)
        avail = result.match.availability_status

        if conf == "high":
            if avail == "sold_out":
                note = _NOTE_HIGH_MATCH_SOLD_OUT
                status = MATCH_STATUS_HIGH
                # High-confidence row but sold-out — don't quote price as
                # final. The response writer should fall back to listing
                # alternative dates.
            else:
                note = _NOTE_HIGH_MATCH
                status = MATCH_STATUS_HIGH

            return SelectedDeparturePlanning(
                web_code=web_code,
                tour_code_real=tour_code_real,
                airline=airline,
                tour_name=tour_name,
                match_status=status,
                confidence="high",
                parsed_phrase_date=parsed_iso,
                matched_departure=match_dict,
                matched_row_availability=avail,
                available_departures=available_dicts,
                ask_confirmation=(avail == "sold_out"),
                safe_planning_note=note,
            )

        # medium / low — ask the customer to confirm before quoting.
        return SelectedDeparturePlanning(
            web_code=web_code,
            tour_code_real=tour_code_real,
            airline=airline,
            tour_name=tour_name,
            match_status=(
                MATCH_STATUS_MEDIUM if conf == "medium" else MATCH_STATUS_LOW
            ),
            confidence=conf,
            parsed_phrase_date=parsed_iso,
            matched_departure=match_dict,
            matched_row_availability=avail,
            available_departures=available_dicts,
            ask_confirmation=True,
            safe_planning_note=_NOTE_MEDIUM_LOW,
        )

    if result.status == "ambiguous":
        candidate_dicts = [compact_departure_dict(c) for c in result.candidates]
        return SelectedDeparturePlanning(
            web_code=web_code,
            tour_code_real=tour_code_real,
            airline=airline,
            tour_name=tour_name,
            match_status=MATCH_STATUS_AMBIGUOUS,
            parsed_phrase_date=parsed_iso,
            ambiguous_candidates=candidate_dicts,
            available_departures=available_dicts,
            ask_confirmation=True,
            safe_planning_note=_NOTE_AMBIGUOUS,
        )

    if result.status == "no_match":
        note = _NOTE_NO_MATCH
        if result.error == "date_in_past":
            note = _NOTE_PAST_DATE
        elif result.error == "no_rows_with_dates":
            note = _NOTE_NO_ROWS
        return SelectedDeparturePlanning(
            web_code=web_code,
            tour_code_real=tour_code_real,
            airline=airline,
            tour_name=tour_name,
            match_status=MATCH_STATUS_NO_MATCH,
            parsed_phrase_date=parsed_iso,
            available_departures=available_dicts,
            safe_planning_note=note,
        )

    # unparseable
    return SelectedDeparturePlanning(
        web_code=web_code,
        tour_code_real=tour_code_real,
        airline=airline,
        tour_name=tour_name,
        match_status=MATCH_STATUS_UNPARSEABLE,
        available_departures=available_dicts,
        safe_planning_note=_NOTE_UNPARSEABLE,
    )
