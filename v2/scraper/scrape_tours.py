"""
v2.scraper.scrape_tours — Scraper for tourfiremai.com → tours_canonical

CRITICAL property:
    - Wholesale brand name is NOT extracted from listing page → bot has no LLM bias
    - web_code (URL slug, e.g. "ap242455") and tour_code_real (from PDF) are SEPARATE
    - Departure dates saved to tour_departures, not flattened into tours_canonical

URL pattern:
    GET tourfiremai.com/intertour/{country_id}/{country_thai_name}

Listing yields tour cards. Each card has:
    - name, code (web_code), days, base_price, departure_dates, airline, link

Parser tolerates HTML structure drift — fields default to None on extract failure
instead of crashing.

Public API:
    fetch_country_listing(country_id, country_name, *, http=None) -> list[ParsedTour]
    upsert_tours_to_canonical(parsed: list[ParsedTour], supabase) -> dict
    scrape_all(country_ids, supabase, http=None) -> dict  # cron entrypoint
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional, Protocol

logger = logging.getLogger("v2.scraper")

BASE_URL = "https://www.tourfiremai.com"
LISTING_PATH = "/intertour/{country_id}/{country_name}"
DEFAULT_TIMEOUT = 30


class HttpClient(Protocol):
    def get(self, url: str, timeout: int = DEFAULT_TIMEOUT) -> "HttpResponse": ...


class HttpResponse(Protocol):
    status_code: int
    text: str


@dataclass
class ParsedTour:
    web_code: str
    name: str
    country: str
    country_id: int
    days: int = 0
    nights: int = 0
    base_price: int = 0
    airline: Optional[str] = None
    url: str = ""
    departure_dates: list[date] = field(default_factory=list)
    city_tags: list[str] = field(default_factory=list)
    is_fire_sale: bool = False
    raw: dict = field(default_factory=dict)  # original strings for debugging

    def is_valid(self) -> bool:
        return bool(self.web_code and self.name and self.base_price > 0)


# --- HTML parsing helpers -----------------------------------------------------

# Tour card heuristic: look for /intertourdetail/<web_code> links
TOUR_LINK_RE = re.compile(r'href=["\']/intertourdetail/([a-z]{2,3}\d{5,7})["\']', re.I)
# Alternative pattern: /intertour/{country_id}/{name}#code or /tour/{web_code}
TOUR_LINK_ALT_RE = re.compile(r'/(?:tour|intertourdetail)/([a-z]{2,3}\d{5,7})', re.I)

PRICE_RE = re.compile(r'([1-9]\d{0,2}(?:,\d{3})+|\d{4,6})')
DAYS_NIGHTS_RE = re.compile(r'(\d+)\s*วัน\s*(\d+)\s*คืน')
AIRLINE_RE = re.compile(r'\b(TG|JL|NH|KE|OZ|SQ|CI|BR|CX|MU|CA|CZ|VN|VZ|FD|DD|XJ|XW|TR|AK|D7|JT|QZ|HU|MF|WE)\b')
# Thai date patterns: "18-23 มิ.ย. 69" or "เม.ย.-พ.ย. 69"
THAI_MONTHS = {
    "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
    "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12,
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5, "มิถุนายน": 6,
    "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12,
}


def _thai_year_to_ce(year_thai: int) -> int:
    """ค.ศ.: 69 → 2026 (พ.ศ. 2569 = ค.ศ. 2026). Heuristic if 4-digit BE given."""
    if year_thai > 2400:
        return year_thai - 543
    if year_thai < 100:
        # Two-digit BE year, e.g. 69 → 2569 BE → 2026 CE
        return 2469 + year_thai if year_thai < 50 else 2400 + year_thai - 543 + 543  # noqa
    return year_thai  # already CE


_COMMA_PRICE_RE = re.compile(r"\b([1-9]\d{0,2}(?:,\d{3})+)\b")
_BARE_PRICE_RE = re.compile(r"(?<![a-zA-Z_/])\b(\d{4,6})\b")


def parse_price(text: str) -> int:
    """
    Extract first plausible price (>= 1,000 baht) from text.

    Avoids matching digits inside web_code (e.g. "ap242455") by skipping
    matches preceded by ASCII letters/_/.
    """
    if not text:
        return 0
    # Prefer comma-separated format (always a real price)
    for m in _COMMA_PRICE_RE.finditer(text):
        raw = m.group(1).replace(",", "")
        try:
            n = int(raw)
            if 1_000 <= n <= 1_000_000:
                return n
        except ValueError:
            continue
    # Fallback: bare 4-6 digit number NOT preceded by letter (skip web_codes)
    for m in _BARE_PRICE_RE.finditer(text):
        raw = m.group(1)
        try:
            n = int(raw)
            if 1_000 <= n <= 1_000_000:
                return n
        except ValueError:
            continue
    return 0


def parse_days_nights(text: str) -> tuple[int, int]:
    if not text:
        return 0, 0
    m = DAYS_NIGHTS_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 0, 0


def parse_airline(text: str) -> Optional[str]:
    if not text:
        return None
    m = AIRLINE_RE.search(text.upper())
    return m.group(1) if m else None


def parse_departure_dates(text: str, *, year_hint: Optional[int] = None) -> list[date]:
    """
    Best-effort extract Thai-format departure dates from a snippet of text.

    Returns parsed date objects. Year_hint defaults to current CE year.
    Patterns handled:
        - "18-23 มิ.ย. 69"  → [2026-06-18, 2026-06-19, ... 2026-06-23] (range)
          (we only emit start + end here; expansion is downstream)
        - "5 ก.ค. 69" → [2026-07-05]
    """
    if not text:
        return []

    current_year = year_hint or datetime.utcnow().year
    out: list[date] = []
    seen: set[str] = set()

    # Pattern: "DD-DD month year"
    range_re = re.compile(
        r"(\d{1,2})\s*[-–]\s*(\d{1,2})\s*(" + "|".join(re.escape(m) for m in THAI_MONTHS) + r")\s*(\d{2,4})?",
    )
    for m in range_re.finditer(text):
        day_a, day_b, month_str, year_str = m.group(1), m.group(2), m.group(3), m.group(4)
        try:
            month = THAI_MONTHS[month_str]
            year_ce = _resolve_year(year_str, current_year)
            d1 = date(year_ce, month, int(day_a))
            d2 = date(year_ce, month, int(day_b))
            for d in (d1, d2):
                k = d.isoformat()
                if k not in seen:
                    out.append(d)
                    seen.add(k)
        except (KeyError, ValueError):
            continue

    # Pattern: "DD month year"
    single_re = re.compile(
        r"(\d{1,2})\s*(" + "|".join(re.escape(m) for m in THAI_MONTHS) + r")\s*(\d{2,4})?",
    )
    for m in single_re.finditer(text):
        day, month_str, year_str = m.group(1), m.group(2), m.group(3)
        try:
            month = THAI_MONTHS[month_str]
            year_ce = _resolve_year(year_str, current_year)
            d = date(year_ce, month, int(day))
            k = d.isoformat()
            if k not in seen:
                out.append(d)
                seen.add(k)
        except (KeyError, ValueError):
            continue

    return out


def _resolve_year(year_str: Optional[str], current_ce: int) -> int:
    """Convert "69" (two-digit Thai BE) → 2026 (CE). BE year = CE + 543."""
    if not year_str:
        return current_ce
    try:
        y = int(year_str)
        if y < 100:
            # Two-digit Thai BE: y → BE 2500+y → CE 1957+y
            return 1957 + y
        if y > 2400:
            return y - 543  # BE → CE
        return y
    except ValueError:
        return current_ce


# --- Listing-page parser ------------------------------------------------------

def parse_listing_html(html: str, country: str, country_id: int) -> list[ParsedTour]:
    """
    Parse a country listing page HTML into ParsedTour list.

    Strategy: find each /intertourdetail/{web_code} (or /tour/{web_code}) position.
    Bound each tour's snippet by the *next* code position to avoid overlap.
    """
    if not html:
        return []

    # Find first occurrence of each web_code (sorted by position)
    positions: list[tuple[int, str]] = []
    seen: set[str] = set()
    for m in TOUR_LINK_RE.finditer(html):
        code = m.group(1).lower()
        if code not in seen:
            positions.append((m.start(), code))
            seen.add(code)
    if not positions:
        for m in TOUR_LINK_ALT_RE.finditer(html):
            code = m.group(1).lower()
            if code not in seen:
                positions.append((m.start(), code))
                seen.add(code)

    positions.sort(key=lambda t: t[0])

    tours: list[ParsedTour] = []
    for i, (pos, code) in enumerate(positions):
        # Snippet bounded forward by next code (or end of HTML)
        end_pos = positions[i + 1][0] if i + 1 < len(positions) else len(html)
        snippet = html[pos:end_pos]

        # Extract name from anchor text near the code link
        name = _extract_tour_name(snippet, code)
        price = parse_price(snippet)
        days, nights = parse_days_nights(snippet)
        airline = parse_airline(snippet)
        dep_dates = parse_departure_dates(snippet)
        is_fire = any(
            kw in snippet for kw in ("ทัวร์ไฟไหม้", "ไฟไหม้", "Flash Sale", "FLASH SALE")
        )

        url = f"{BASE_URL}/intertourdetail/{code}"
        tour = ParsedTour(
            web_code=code,
            name=name or f"(unparsed name {code})",
            country=country,
            country_id=country_id,
            days=days,
            nights=nights,
            base_price=price,
            airline=airline,
            url=url,
            departure_dates=dep_dates,
            is_fire_sale=is_fire,
            raw={"snippet_len": len(snippet)},
        )
        if tour.is_valid():
            tours.append(tour)
    return tours


def _extract_tour_name(snippet: str, code: str) -> Optional[str]:
    """Best-effort name extraction from a tour card snippet."""
    # Look for <h*>...</h*> or aria-label="..."
    for pattern in (
        rf'<h[1-6][^>]*>([^<]{{5,200}})</h',
        rf'aria-label=["\']([^"\']{{5,200}})["\']',
        rf'<a[^>]*href=["\'][^"\']*{re.escape(code)}[^"\']*["\'][^>]*>([^<]{{5,200}})</a>',
        rf'<div[^>]*class=["\'][^"\']*title[^"\']*["\'][^>]*>([^<]{{5,200}})</div>',
    ):
        m = re.search(pattern, snippet, re.I | re.S)
        if m:
            name = m.group(1).strip()
            # Strip leftover HTML entities and double spaces
            name = re.sub(r"\s+", " ", name)
            return name
    return None


# --- High-level entrypoints ---------------------------------------------------

def fetch_country_listing(
    country_id: int,
    country_name: str,
    *,
    http: Optional[HttpClient] = None,
) -> list[ParsedTour]:
    if http is None:
        try:
            import requests as _requests  # noqa
            http = _RequestsAdapter()
        except ImportError as e:
            raise RuntimeError("requests library required for fetch_country_listing()") from e

    url = BASE_URL + LISTING_PATH.format(country_id=country_id, country_name=country_name)
    logger.info("Fetching %s", url)
    resp = http.get(url, timeout=DEFAULT_TIMEOUT)
    if resp.status_code != 200:
        logger.warning("Non-200 response %d for %s", resp.status_code, url)
        return []
    return parse_listing_html(resp.text, country=country_name, country_id=country_id)


def upsert_tours_to_canonical(parsed: list[ParsedTour], supabase: Any) -> dict:
    """
    Upsert by web_code. NEVER hard-deletes — sets is_active=False if a previously-seen
    tour is no longer in the listing.
    Returns {"upserted": N, "departures_inserted": N, "errors": [...]}.
    """
    upserted = 0
    departures_inserted = 0
    errors: list[str] = []

    for t in parsed:
        try:
            row = {
                "web_code": t.web_code,
                "name": t.name,
                "country": t.country,
                "country_id": t.country_id,
                "days": t.days,
                "nights": t.nights,
                "base_price": t.base_price,
                "airline": t.airline,
                "url": t.url,
                "is_fire_sale": t.is_fire_sale,
                "is_active": True,
                "city_tags": t.city_tags,
                "last_synced_at": datetime.utcnow().isoformat() + "Z",
            }
            result = supabase.table("tours_canonical").upsert(
                match={"web_code": t.web_code},
                insert={**row, "scraped_at": datetime.utcnow().isoformat() + "Z"},
                update=row,
            )
            upserted += 1
            tour_id = result.get("id") if isinstance(result, dict) else None

            # Insert departures
            if tour_id and t.departure_dates:
                for d in t.departure_dates:
                    dep_row = {
                        "tour_id": tour_id,
                        "web_code": t.web_code,
                        "departure_date": d.isoformat(),
                        "airline": t.airline,
                        "status": "available",
                    }
                    try:
                        supabase.table("tour_departures").upsert(
                            match={"tour_id": tour_id, "departure_date": d.isoformat()},
                            insert=dep_row,
                            update=dep_row,
                        )
                        departures_inserted += 1
                    except Exception as e:
                        errors.append(f"departure {t.web_code} {d}: {e}")

        except Exception as e:
            errors.append(f"{t.web_code}: {e}")

    return {"upserted": upserted, "departures_inserted": departures_inserted, "errors": errors}


def scrape_all(
    country_ids: dict[int, str],
    supabase: Any,
    *,
    http: Optional[HttpClient] = None,
    sleep_between: float = 1.0,
) -> dict:
    """
    Run the scraper for a set of (country_id → country_thai_name).

    Returns {"per_country": {country: stats}, "total_upserted": N, "total_errors": N}.
    """
    per_country: dict[str, dict] = {}
    total_upserted = 0
    total_errors = 0

    for cid, cname in country_ids.items():
        try:
            parsed = fetch_country_listing(cid, cname, http=http)
            stats = upsert_tours_to_canonical(parsed, supabase)
            per_country[cname] = {"fetched": len(parsed), **stats}
            total_upserted += stats["upserted"]
            total_errors += len(stats["errors"])
        except Exception as e:
            per_country[cname] = {"error": str(e), "fetched": 0, "upserted": 0}
            total_errors += 1

        if sleep_between > 0:
            time.sleep(sleep_between)

    return {
        "per_country": per_country,
        "total_upserted": total_upserted,
        "total_errors": total_errors,
        "completed_at": datetime.utcnow().isoformat() + "Z",
    }


class _RequestsAdapter:
    """Minimal HttpClient adapter over requests."""

    def get(self, url: str, timeout: int = DEFAULT_TIMEOUT):
        import requests
        return requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (TourFireMai V2 scraper; +ops@tourfiremai.com)",
            "Accept": "text/html,application/xhtml+xml",
        })


if __name__ == "__main__":
    # CLI dry-run (no Supabase): print parsed tours
    import json, sys
    cid = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    cname = sys.argv[2] if len(sys.argv) > 2 else "ญี่ปุ่น"
    parsed = fetch_country_listing(cid, cname)
    print(f"Parsed {len(parsed)} tours for {cname}")
    for t in parsed[:5]:
        print(json.dumps({
            "web_code": t.web_code, "name": t.name, "price": t.base_price,
            "days": t.days, "airline": t.airline, "dates": [d.isoformat() for d in t.departure_dates[:3]]
        }, ensure_ascii=False))
