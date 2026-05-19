"""
v2.scraper.departure_price_table — Deterministic parser for the detail-page
departure price table on tourfiremai.com/tour/<web_code>.

Sprint 5 Package F (DEV-2026-05-20-012).

Why this exists
---------------
The listing page (parsed by v2.scraper.scrape_tours) is enough to find Top 3
candidates, but booking-stage answers need the exact per-departure row from the
detail page:

    - departure date range can be quoted to the customer ("18-23 มิ.ย. 69")
    - adult_price differs by date and may not match the listing's base_price
    - single_supplement / joinland / child / bus / group_size live on this row
    - status text is preserved verbatim — sold-out / full come from
      tour_availability_overrides (migration 020), NOT from button copy

Live HTML structure
-------------------
- Container          : <div class="table-dateprice"> ... </div>
- Row wrapper        : <div class="b-tb-dp"> ... </div>
- Cells (left→right) : s-tb1-n .. s-tb9-n
                       1: date range (Thai BE)
                       2: bus number
                       3: adult price
                       4: child with bed
                       5: child no bed
                       6: single supplement
                       7: joinland
                       8: group size
                       9: status / contact button text
- Header tour code   : <span class="b-codepg">BCCKG27-HU</span> (tour_code_real)

Hard rules (per CURRENT_DEV_TASK.md)
------------------------------------
- Use /tour/<web_code>, never /intertourdetail/<web_code> (the latter 500s).
- "-", empty, or a contact-button placeholder parses to None (NOT 0).
- Status text is preserved verbatim. Generic contact copy ("ติดต่อเจ้าหน้า…")
  is NEVER interpreted as sold-out. availability_status defaults to
  "unknown" unless a clear sold-out class is present on the row.
- web_code (ap242455), tour_code_real (BCCKG27-HU), and airline (HU) are
  kept strictly separate. The parser never invents or merges them.
- No network, no LLM, no OCR, no Supabase. Pure parsing on HTML strings.

Public API
----------
    DeparturePriceRow                                  -- dataclass for one row
    parse_departure_price_table(html, web_code, ...)  -- list[DeparturePriceRow]
    parse_detail_header_codes(html)                   -- dict
    to_tour_departure_rows(rows, tour_id=None)        -- list[dict] adapter
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Any, Optional

logger = logging.getLogger("v2.scraper.departure_price_table")

BASE_URL = "https://www.tourfiremai.com"
DETAIL_PATH = "/tour/{web_code}"

# Cell placeholders that map to None, not 0.
MISSING_PRICE_TOKENS: tuple[str, ...] = ("", "-", "–", "—", "_", "N/A", "n/a", "ไม่มี", "ไม่ระบุ")

# Generic contact-button phrases that must NOT be interpreted as sold-out.
# (Keep this list narrow on purpose — sold-out is decided by class signals or
# tour_availability_overrides, not by button copy.)
CONTACT_PHRASES: tuple[str, ...] = (
    "ติดต่อเจ้าหน้า",      # ติดต่อเจ้าหน้าที่
    "ติดต่อเจ้าหน้าที่",
    "ติดต่อสอบถาม",
    "สอบถามที่นั่ง",
    "สอบถาม",
    "Inquire",
)

# Class fragments that *do* indicate row-level sold-out / full / closed.
SOLD_OUT_CLASS_FRAGMENTS: tuple[str, ...] = (
    "sold-out",
    "soldout",
    "full",
    "closed",
    "เต็ม",
)

# Reuse the listing parser's month map so future drift fixes happen in one place.
THAI_MONTHS: dict[str, int] = {
    # short
    "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
    "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12,
    # long
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5, "มิถุนายน": 6,
    "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12,
}

# Airline IATA codes the bot already knows (mirror of scrape_tours.AIRLINE_RE).
AIRLINE_TOKEN_RE = re.compile(
    r"\b(TG|JL|NH|KE|OZ|SQ|CI|BR|CX|MU|CA|CZ|VN|VZ|FD|DD|XJ|XW|TR|AK|D7|JT|QZ|HU|MF|WE)\b"
)

# Strict web_code shape: 2-3 lowercase letters + 5-7 digits (e.g. ap242455).
WEB_CODE_RE = re.compile(r"\b([a-z]{2,3}\d{5,7})\b", re.I)

# tour_code_real shape: ALLCAPS letters/digits/underscores, optionally with a
# dash suffix, e.g. BCCKG27-HU, BT-NRT_S15_XJ, TFUEU0626. The pre-dash segment
# can be as short as 2 letters (BT-NRT...), while non-dash codes need 3+ chars
# so bare airline tokens are still rejected by _looks_like_airline_or_webcode.
TOUR_CODE_REAL_RE = re.compile(
    r"\b([A-Z][A-Z0-9_]{1,}(?:-[A-Z0-9_]{1,16})+|[A-Z][A-Z0-9_]{2,})\b"
)


@dataclass
class DeparturePriceRow:
    """One parsed row from the detail-page departure price table.

    Missing prices/integers are stored as None (never 0). status_text is
    preserved verbatim from the page; availability_status is the parser's
    cautious classification (one of: "available", "sold_out", "unknown").
    """

    web_code: str
    tour_code_real: Optional[str] = None
    departure_start: Optional[date] = None
    departure_end: Optional[date] = None
    departure_label_raw: Optional[str] = None
    bus: Optional[int] = None
    adult_price: Optional[int] = None
    child_bed_price: Optional[int] = None
    child_no_bed_price: Optional[int] = None
    single_supplement_price: Optional[int] = None
    joinland_price: Optional[int] = None
    group_size: Optional[int] = None
    status_text: Optional[str] = None
    status_class: Optional[str] = None
    availability_status: str = "unknown"
    source_url: Optional[str] = None
    airline: Optional[str] = None
    raw_cells: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("departure_start", "departure_end"):
            v = d.get(k)
            if isinstance(v, date):
                d[k] = v.isoformat()
        return d


# ---------------------------------------------------------------------------
# Header parsing (tour_code_real, airline, web_code from .b-codepg / page)
# ---------------------------------------------------------------------------

_CODEPG_BLOCK_RE = re.compile(
    r'<[^>]+class\s*=\s*["\'][^"\']*\bb-codepg\b[^"\']*["\'][^>]*>(.*?)</',
    re.I | re.S,
)
_CODEPG_VALUE_RE = re.compile(
    r'<div\b[^>]*class\s*=\s*["\'][^"\']*\bb-codepg\b[^"\']*["\'][^>]*>'
    r'.*?<p\b[^>]*class\s*=\s*["\'][^"\']*\btxt-pd-l\b[^"\']*["\'][^>]*>(.*?)</p>',
    re.I | re.S,
)


def parse_detail_header_codes(html: str) -> dict[str, Optional[str]]:
    """Extract tour_code_real, airline, and (best-effort) web_code from the
    detail page header. Never merges these three fields.

    Returns dict with keys {"tour_code_real", "airline", "web_code"} — each
    value may be None when not confidently extractable.
    """
    out: dict[str, Optional[str]] = {
        "tour_code_real": None,
        "airline": None,
        "web_code": None,
    }
    if not html:
        return out

    code_block = ""
    value_match = _CODEPG_VALUE_RE.search(html)
    if value_match:
        code_block = _strip_tags(value_match.group(1))
    else:
        m = _CODEPG_BLOCK_RE.search(html)
        if m:
            code_block = _strip_tags(m.group(1))

    # tour_code_real: prefer text inside .b-codepg; fall back to full header search
    # only if the codepg block is empty.
    text_for_real = code_block or _strip_tags(html[: min(len(html), 4000)])
    tcr_match = TOUR_CODE_REAL_RE.search(text_for_real)
    if tcr_match:
        candidate = tcr_match.group(1).strip()
        # Reject obvious airline tokens or pure web_codes that slipped through.
        if not _looks_like_airline_or_webcode(candidate):
            out["tour_code_real"] = candidate

    # airline: take the FIRST airline token from the codepg block (or page head).
    # Avoid trailing characters by anchoring on word boundaries.
    air_search_text = code_block or text_for_real
    am = AIRLINE_TOKEN_RE.search(air_search_text)
    if am:
        out["airline"] = am.group(1)

    # Cross-check: if tour_code_real contains a known airline token in its
    # dash/underscore-separated suffix (e.g. BCCKG27-HU, BT-NRT_S15_XJ),
    # mirror it into airline only when airline was still None.
    if out["tour_code_real"] and out["airline"] is None:
        out["airline"] = _airline_from_tour_code(out["tour_code_real"])

    # web_code: scan whole document; first hit wins.
    wm = WEB_CODE_RE.search(html)
    if wm:
        out["web_code"] = wm.group(1).lower()

    return out


def _looks_like_airline_or_webcode(s: str) -> bool:
    """True if `s` is just an airline token (HU) or a web_code (ap242455)."""
    if AIRLINE_TOKEN_RE.fullmatch(s):
        return True
    if WEB_CODE_RE.fullmatch(s):
        return True
    return False


def _airline_from_tour_code(tour_code_real: str) -> Optional[str]:
    """Best-effort airline extraction from a real tour code suffix."""
    for token in reversed(re.split(r"[-_]", tour_code_real)):
        if AIRLINE_TOKEN_RE.fullmatch(token):
            return token
    return None


# ---------------------------------------------------------------------------
# Row table parsing
# ---------------------------------------------------------------------------

_TABLE_BLOCK_RE = re.compile(
    r'<div\b[^>]*class\s*=\s*["\'][^"\']*\btable-dateprice\b[^"\']*["\'][^>]*>(.*)',
    re.I | re.S,
)
_ROW_BLOCK_RE = re.compile(
    r'<div\b[^>]*class\s*=\s*["\']([^"\']*\bb-tb-dp\b[^"\']*)["\'][^>]*>(.*?)(?=<div\b[^>]*class\s*=\s*["\'][^"\']*\bb-tb-dp\b|$)',
    re.I | re.S,
)
_CELL_BLOCK_RE = re.compile(
    r'<(?:div|span)\b[^>]*class\s*=\s*["\']([^"\']*\bs-tb(\d)-n\b[^"\']*)["\'][^>]*>(.*?)</(?:div|span)>',
    re.I | re.S,
)


def parse_departure_price_table(
    html: str,
    web_code: str,
    source_url: Optional[str] = None,
) -> list[DeparturePriceRow]:
    """Parse all departure rows on a detail page into DeparturePriceRow list.

    Pure function: no network, no DB, no LLM. Tolerates missing cells.
    """
    if not html or not web_code:
        return []

    header = parse_detail_header_codes(html)
    tour_code_real = header.get("tour_code_real")
    airline = header.get("airline")

    src = source_url or BASE_URL + DETAIL_PATH.format(web_code=web_code)

    # Try to isolate the price-table block; fall back to whole HTML if the
    # outer markup ever drifts and the regex misses.
    tbl_match = _TABLE_BLOCK_RE.search(html)
    body = tbl_match.group(1) if tbl_match else html

    rows: list[DeparturePriceRow] = []
    for rm in _ROW_BLOCK_RE.finditer(body):
        row_classes = rm.group(1)
        row_html = rm.group(2)
        parsed = _parse_one_row(
            row_html=row_html,
            row_classes=row_classes,
            web_code=web_code.lower(),
            tour_code_real=tour_code_real,
            airline=airline,
            source_url=src,
        )
        if parsed is not None:
            rows.append(parsed)

    return rows


def _parse_one_row(
    *,
    row_html: str,
    row_classes: str,
    web_code: str,
    tour_code_real: Optional[str],
    airline: Optional[str],
    source_url: str,
) -> Optional[DeparturePriceRow]:
    cells: dict[int, tuple[str, str]] = {}
    for cm in _CELL_BLOCK_RE.finditer(row_html):
        cell_classes = cm.group(1)
        idx = int(cm.group(2))
        inner = cm.group(3)
        text = _strip_tags(inner).strip()
        cells[idx] = (cell_classes, text)

    if not cells:
        return None

    raw_cells = [cells.get(i, ("", ""))[1] for i in range(1, 10)]
    label = cells.get(1, ("", ""))[1]
    bus = _parse_int_or_none(cells.get(2, ("", ""))[1])
    adult = _parse_money_or_none(cells.get(3, ("", ""))[1])
    child_bed = _parse_money_or_none(cells.get(4, ("", ""))[1])
    child_no_bed = _parse_money_or_none(cells.get(5, ("", ""))[1])
    single_supp = _parse_money_or_none(cells.get(6, ("", ""))[1])
    joinland = _parse_money_or_none(cells.get(7, ("", ""))[1])
    group_size = _parse_int_or_none(cells.get(8, ("", ""))[1])
    status_classes, status_text_raw = cells.get(9, ("", ""))
    status_text = status_text_raw or None

    dep_start, dep_end = parse_thai_date_range(label) if label else (None, None)
    availability = _classify_availability(row_classes, status_classes, status_text_raw)

    return DeparturePriceRow(
        web_code=web_code,
        tour_code_real=tour_code_real,
        departure_start=dep_start,
        departure_end=dep_end,
        departure_label_raw=label or None,
        bus=bus,
        adult_price=adult,
        child_bed_price=child_bed,
        child_no_bed_price=child_no_bed,
        single_supplement_price=single_supp,
        joinland_price=joinland,
        group_size=group_size,
        status_text=status_text,
        status_class=status_classes or None,
        availability_status=availability,
        source_url=source_url,
        airline=airline,
        raw_cells=raw_cells,
    )


# ---------------------------------------------------------------------------
# Cell-level parsers
# ---------------------------------------------------------------------------

_MONEY_RE = re.compile(r"(-?\d{1,3}(?:,\d{3})+|-?\d{3,7})")


def _parse_money_or_none(text: str) -> Optional[int]:
    """Parse a money cell ("25,900", "9990", "-") into int, or None when
    missing. Never returns 0 for a "-" cell."""
    if text is None:
        return None
    stripped = text.strip()
    if stripped in MISSING_PRICE_TOKENS:
        return None
    # Reject obvious non-numeric cells like a button label.
    if not any(ch.isdigit() for ch in stripped):
        return None
    m = _MONEY_RE.search(stripped)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        value = int(raw)
    except ValueError:
        return None
    # Sanity bounds: anything outside this range is almost certainly noise
    # (e.g. group_size or a stray "0") — return None so the field stays
    # missing rather than silently corrupting a quote.
    if value <= 0 or value > 1_000_000:
        return None
    return value


def _parse_int_or_none(text: str) -> Optional[int]:
    """Parse a small integer cell (bus, group_size). "-" → None, "0" → None."""
    if text is None:
        return None
    stripped = text.strip()
    if stripped in MISSING_PRICE_TOKENS:
        return None
    m = re.search(r"-?\d+", stripped)
    if not m:
        return None
    try:
        value = int(m.group(0))
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def _classify_availability(
    row_classes: str,
    cell_classes: str,
    status_text: str,
) -> str:
    """Return one of {"available", "sold_out", "unknown"}.

    We are intentionally cautious: a contact-button cell stays "unknown" so
    that the admin tour_availability_overrides table is the source of truth
    for sold-out signals (Hard Rule #5 in CURRENT_DEV_TASK.md).
    """
    haystack = " ".join((row_classes or "", cell_classes or "")).lower()
    for frag in SOLD_OUT_CLASS_FRAGMENTS:
        if frag in haystack:
            return "sold_out"
    text = (status_text or "").strip()
    if not text:
        return "unknown"
    # If the cell is literally an empty placeholder, treat as unknown.
    if text in MISSING_PRICE_TOKENS:
        return "unknown"
    # Generic contact button copy → unknown, NEVER sold_out.
    for phrase in CONTACT_PHRASES:
        if phrase in text:
            return "unknown"
    # Explicit "ว่าง" / "available" wording → available.
    lowered = text.lower()
    if "ว่าง" in text or "available" in lowered or "open" in lowered:
        return "available"
    # Otherwise keep it cautious.
    return "unknown"


# ---------------------------------------------------------------------------
# Thai date range parser
# ---------------------------------------------------------------------------

_MONTH_PATTERN = "|".join(re.escape(m) for m in THAI_MONTHS)

# Full range with month on both sides:
# "04 มิ.ย. 69 - 08 มิ.ย. 69", "29 ธ.ค. 68 - 4 ม.ค. 69".
_RANGE_FULL_RE = re.compile(
    rf"(\d{{1,2}})\s*({_MONTH_PATTERN})\s*(\d{{2,4}})?\s*[-–—~]\s*"
    rf"(\d{{1,2}})\s*({_MONTH_PATTERN})\s*(\d{{2,4}})?",
)

# Two-month range: "29 ก.ค. - 4 ส.ค. 69" (year only on the right side).
_RANGE_TWO_MONTH_RE = re.compile(
    rf"(\d{{1,2}})\s*({_MONTH_PATTERN})\s*[-–—~]\s*(\d{{1,2}})\s*({_MONTH_PATTERN})\s*(\d{{2,4}})?",
)
# Same-month range: "18-23 มิ.ย. 69".
_RANGE_SAME_MONTH_RE = re.compile(
    rf"(\d{{1,2}})\s*[-–—~]\s*(\d{{1,2}})\s*({_MONTH_PATTERN})\s*(\d{{2,4}})?",
)
# Single date: "5 ก.ค. 69".
_SINGLE_RE = re.compile(
    rf"(\d{{1,2}})\s*({_MONTH_PATTERN})\s*(\d{{2,4}})?",
)


def parse_thai_date_range(
    text: str,
    *,
    year_hint: Optional[int] = None,
) -> tuple[Optional[date], Optional[date]]:
    """Parse a Thai-locale departure cell into (start, end) Gregorian dates.

    Handles:
      - same-month range  "18-23 มิ.ย. 69" → (2026-06-18, 2026-06-23)
      - cross-month range "29 ก.ค. - 4 ส.ค. 69" → (2026-07-29, 2026-08-04)
      - cross-year range  "29 ธ.ค. 68 - 4 ม.ค. 69" → (2025-12-29, 2026-01-04)
      - single date       "5 ก.ค. 69" → (2026-07-05, 2026-07-05)

    BE year suffix ("69") parses to Gregorian 2026 via _resolve_year.
    Returns (None, None) when the text is unparseable.
    """
    if not text:
        return None, None

    current_year = year_hint or datetime.utcnow().year

    # 1. Full two-sided range with optional year on either side.
    m = _RANGE_FULL_RE.search(text)
    if m:
        d1, mo1, yr1, d2, mo2, yr2 = m.groups()
        try:
            month_start = THAI_MONTHS[mo1]
            month_end = THAI_MONTHS[mo2]
            year_end = _resolve_year(yr2 or yr1, current_year)
            if yr1:
                year_start = _resolve_year(yr1, current_year)
            else:
                year_start = year_end - 1 if month_start > month_end else year_end
            return (
                date(year_start, month_start, int(d1)),
                date(year_end, month_end, int(d2)),
            )
        except (KeyError, ValueError):
            pass

    # 2. Two-month range
    m = _RANGE_TWO_MONTH_RE.search(text)
    if m:
        d1, mo1, d2, mo2, yr = m.groups()
        try:
            month_end = THAI_MONTHS[mo2]
            month_start = THAI_MONTHS[mo1]
            year_end = _resolve_year(yr, current_year)
            # If start month is "after" end month within the same year string,
            # treat start as previous year (Dec→Jan rollover).
            year_start = year_end - 1 if month_start > month_end else year_end
            return (
                date(year_start, month_start, int(d1)),
                date(year_end, month_end, int(d2)),
            )
        except (KeyError, ValueError):
            pass

    # 3. Same-month range
    m = _RANGE_SAME_MONTH_RE.search(text)
    if m:
        d1, d2, mo, yr = m.groups()
        try:
            month = THAI_MONTHS[mo]
            year = _resolve_year(yr, current_year)
            return (
                date(year, month, int(d1)),
                date(year, month, int(d2)),
            )
        except (KeyError, ValueError):
            pass

    # 4. Single date
    m = _SINGLE_RE.search(text)
    if m:
        d1, mo, yr = m.groups()
        try:
            month = THAI_MONTHS[mo]
            year = _resolve_year(yr, current_year)
            d = date(year, month, int(d1))
            return d, d
        except (KeyError, ValueError):
            pass

    return None, None


def _resolve_year(year_str: Optional[str], current_ce: int) -> int:
    """Convert Thai BE year (any of: '69', '2569') to Gregorian (2026)."""
    if not year_str:
        return current_ce
    try:
        y = int(year_str)
    except ValueError:
        return current_ce
    if y < 100:
        # 2-digit Thai BE: 2500+y → CE 1957+y
        return 1957 + y
    if y > 2400:
        return y - 543
    return y


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_ENTITIES = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&quot;": '"',
    "&#39;": "'",
    "&lt;": "<",
    "&gt;": ">",
}


def _strip_tags(html: str) -> str:
    if not html:
        return ""
    out = _TAG_RE.sub(" ", html)
    for k, v in _ENTITIES.items():
        out = out.replace(k, v)
    out = _WS_RE.sub(" ", out)
    return out.strip()


# ---------------------------------------------------------------------------
# Adapter: parsed rows → tour_departures upsert payload
# ---------------------------------------------------------------------------

_LEGACY_STATUS_BY_AVAILABILITY = {
    "sold_out": "sold_out",
    "available": "available",
    "unknown": "available",  # cautious default until override flips it
}


def to_tour_departure_rows(
    rows: list[DeparturePriceRow],
    *,
    tour_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Convert parsed rows to a list of upsert payloads compatible with the
    existing `tour_departures` shape (post migration 021).

    Rules:
      - Idempotent key suggestion: (tour_id or web_code, departure_start,
        departure_end, bus). The caller decides how to upsert; this helper
        just shapes the payload.
      - Mirrors `adult_price → price` and `departure_start → departure_date`,
        `departure_end → return_date` so legacy reads keep working.
      - Never maps None to 0. Missing prices stay null.
      - Does NOT destroy or override existing rows; that is the upsert
        layer's responsibility.
    """
    payloads: list[dict[str, Any]] = []
    for r in rows:
        if r.departure_start is None:
            # Skip rows with no usable date; otherwise we'd pollute the table
            # with NULL date entries that violate the unique index.
            continue
        payload: dict[str, Any] = {
            "web_code": r.web_code,
            "tour_code_real": r.tour_code_real,
            "airline": r.airline,
            # New, detailed columns (migration 021)
            "departure_start": r.departure_start.isoformat(),
            "departure_end": r.departure_end.isoformat() if r.departure_end else None,
            "departure_label_raw": r.departure_label_raw,
            "bus": r.bus,
            "adult_price": r.adult_price,
            "child_bed_price": r.child_bed_price,
            "child_no_bed_price": r.child_no_bed_price,
            "single_supplement_price": r.single_supplement_price,
            "joinland_price": r.joinland_price,
            "group_size": r.group_size,
            "status_text": r.status_text,
            "status_class": r.status_class,
            "availability_status": r.availability_status,
            "source_url": r.source_url,
            # Legacy mirrors so pre-migration reads remain valid
            "departure_date": r.departure_start.isoformat(),
            "return_date": r.departure_end.isoformat() if r.departure_end else None,
            "price": r.adult_price,
            "status": _LEGACY_STATUS_BY_AVAILABILITY.get(
                r.availability_status, "available"
            ),
        }
        if tour_id is not None:
            payload["tour_id"] = tour_id
        payloads.append(payload)
    return payloads


def idempotency_key(
    payload: dict[str, Any],
    *,
    tour_id: Optional[str] = None,
) -> tuple[Any, ...]:
    """Return a deterministic key for upsert deduplication.

    Spec: (tour_id or web_code, departure_start, departure_end, bus).
    """
    return (
        tour_id or payload.get("tour_id") or payload.get("web_code"),
        payload.get("departure_start"),
        payload.get("departure_end"),
        payload.get("bus"),
    )
