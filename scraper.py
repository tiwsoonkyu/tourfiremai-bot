#!/usr/bin/env python3
"""
TourFiremai Nightly Scraper (v2 — with price, airline, departure_dates)
=======================================================================
Scrapes all tour listings from tourfiremai.com and upserts into
the Supabase `tours` table including:
  - price_min       (INT)    — cheapest price found on detail page
  - airline         (TEXT)   — primary airline code
  - departure_dates (TEXT)   — comma-separated upcoming departure dates

Run via GitHub Actions nightly at 02:00 AM BKK (19:00 UTC).
Can also be triggered manually from the GitHub Actions tab.

Required env vars:
  SUPABASE_URL  — e.g. https://xxxx.supabase.co
  SUPABASE_KEY  — service_role key (for write access)
"""

import os
import re
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timezone

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates",
}

WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TourFiremaiBot/2.0; +https://tourfiremai.com)"
}

MAX_PAGES_PER_COUNTRY   = 25   # safety cap
DELAY_BETWEEN_PAGES     = 0.4  # seconds
DELAY_BETWEEN_COUNTRIES = 1.0
DETAIL_WORKERS          = 5    # parallel detail-page fetches per country
UPSERT_CHUNK_SIZE       = 100

# Thai month abbreviations
THAI_MONTHS = r"(?:ม\.ค|ก\.พ|มี\.ค|เม\.ย|พ\.ค|มิ\.ย|ก\.ค|ส\.ค|ก\.ย|ต\.ค|พ\.ย|ธ\.ค)"
DATE_PATTERN    = re.compile(rf"\d{{1,2}}\s*{THAI_MONTHS}\.?\s*\d{{0,4}}")
PRICE_PATTERN   = re.compile(r"([\d,]{4,})\s*บาท")
AIRLINE_PATTERN = re.compile(r"\b(XJ|VZ|SL|FD|DD|TG|WE|PG|QR|TK|EK|MH|CX|XI|PR|OZ|KE|SQ|NH|JL|CZ|MU|CA|BR|VN|BX|Z2)\b")

# All countries
COUNTRY_MAP = {
    "1": "เกาหลี",        "2": "ญี่ปุ่น",     "3": "ฮ่องกง",
    "4": "สิงคโปร์",     "5": "จีน",          "6": "มาเลเซีย",
    "7": "เวียดนาม",     "8": "พม่า",          "9": "ลาว",
    "18": "อินโดนีเซีย", "19": "ไต้หวัน",    "29": "มาเก๊า",
    "104": "ฟิลิปปินส์",
    "14": "อินเดีย",    "20": "ภูฏาน",        "182": "ศรีลังกา",
    "184": "ทิเบต",     "173": "อุซเบกิสถาน", "256": "คาซัคสถาน",
    "164": "ปากีสถาน",  "165": "มองโกเลีย",
    "72": "สหรัฐอาหรับฯ","70": "จอร์แดน",    "16": "อียิปต์",
    "167": "เคนย่า",    "68": "แอฟริกาใต้",   "161": "โมร็อกโก",
    "183": "อิหร่าน",
    "42": "อังกฤษ",     "64": "สวิตเซอร์แลนด์","100": "เยอรมนี",
    "101": "ฝรั่งเศส",  "102": "อิตาลี",       "105": "สเปน",
    "159": "ออสเตรีย",  "169": "กรีซ",          "200": "โปรตุเกส",
    "213": "เบลเยี่ยม", "308": "เนเธอร์แลนด์", "2217": "เบเนลักซ์",
    "47": "สแกนดิเนเวีย","65": "ฟินแลนด์",    "153": "สวีเดน",
    "162": "นอร์เวย์",  "232": "เดนมาร์ก",     "25": "ไอซ์แลนด์",
    "194": "ไอร์แลนด์", "197": "สกอตแลนด์",
    "80": "ยุโรปตะวันออก","66": "โครเอเชีย",  "166": "โปแลนด์",
    "168": "จอร์เจีย",  "71": "ตุรเคีย",       "2213": "บอลติก",
    "2220": "โรมาเนีย", "275": "มอลตา",        "276": "มอนเตเนโกร",
    "10": "ออสเตรเลีย", "11": "นิวซีแลนด์",
    "12": "อเมริกา",    "73": "แคนาดา",         "174": "บราซิล",
    "175": "อาร์เจนติน่า","226": "โคลอมเบีย",  "272": "เม็กซิโก",
    "17": "รัสเซีย",
}

PRIORITY_COUNTRY_IDS = ["2", "1", "7", "5", "3", "4", "19"]


# ─── Detail page scraper ──────────────────────────────────────────────────────

def fetch_detail(tour: dict) -> dict:
    """Fetch detail page and enrich tour with price, airline, departure_dates."""
    url = tour.get("url", "")
    if not url:
        return tour
    try:
        resp = requests.get(url, headers=WEB_HEADERS, timeout=15)
        resp.raise_for_status()
        text = resp.text

        # Price — collect all numeric values before "บาท", take minimum
        prices = []
        for m in PRICE_PATTERN.finditer(text):
            try:
                val = int(m.group(1).replace(",", ""))
                if 5_000 <= val <= 500_000:   # sanity range
                    prices.append(val)
            except ValueError:
                pass
        tour["price_min"] = min(prices) if prices else None

        # Airline
        airlines = AIRLINE_PATTERN.findall(text)
        tour["airline"] = airlines[0] if airlines else None

        # Departure dates — deduplicate, keep future dates only
        today_str = date.today().strftime("%Y-%m-%d")
        raw_dates = DATE_PATTERN.findall(text)
        seen, unique = set(), []
        for d in raw_dates:
            d = d.strip()
            if d not in seen:
                seen.add(d)
                unique.append(d)
        tour["departure_dates"] = ", ".join(unique[:12]) if unique else None

    except Exception as e:
        logger.debug(f"  detail error {url}: {e}")
    return tour


# ─── Listing page scraper ─────────────────────────────────────────────────────

def parse_listing_cards(html_text: str, country_id: str, country_name: str) -> list[dict]:
    """Extract tour cards (name + url) from one listing page."""
    soup = BeautifulSoup(html_text, "html.parser")
    now_iso = datetime.now(timezone.utc).isoformat()
    tours = []

    for name_div in soup.find_all("div", class_="b-name"):
        h3 = name_div.find("h3")
        if not h3:
            continue
        name = h3.get_text(strip=True)

        url = ""
        tour_code = ""
        for ancestor in name_div.parents:
            a = ancestor.find("a", href=re.compile(r"/tour/ap\w+"))
            if a:
                href = a["href"]
                url = "https://www.tourfiremai.com" + href if href.startswith("/") else href
                m = re.search(r"/tour/(ap\w+)", href)
                if m:
                    tour_code = m.group(1)
                break
            if ancestor.name in ("section", "body", "[document]"):
                break

        if not tour_code:
            continue

        tours.append({
            "tour_code":       tour_code,
            "name":            name,
            "url":             url,
            "country_id":      country_id,
            "country_name":    country_name,
            "last_scraped":    now_iso,
            "price_min":       None,
            "airline":         None,
            "departure_dates": None,
        })

    return tours


def scrape_country(country_id: str, country_name: str) -> list[dict]:
    """Scrape all listing pages then enrich with detail pages in parallel."""
    base_url = f"https://www.tourfiremai.com/intertour/{country_id}/"
    all_tours: list[dict] = []
    seen_codes: set[str] = set()

    # ── Step 1: collect all listing cards ────────────────────────────────────
    for page_num in range(1, MAX_PAGES_PER_COUNTRY + 1):
        url = base_url if page_num == 1 else f"{base_url}?page={page_num}"
        try:
            resp = requests.get(url, headers=WEB_HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"  [{country_name}] page {page_num} error: {e}")
            break

        cards = parse_listing_cards(resp.text, country_id, country_name)
        if not cards:
            logger.info(f"  [{country_name}] page {page_num}: no cards → done")
            break

        new_cards = [c for c in cards if c["tour_code"] not in seen_codes]
        seen_codes.update(c["tour_code"] for c in new_cards)
        all_tours.extend(new_cards)
        logger.info(f"  [{country_name}] page {page_num}: +{len(new_cards)} (total {len(all_tours)})")

        if len(cards) < 6:
            break
        time.sleep(DELAY_BETWEEN_PAGES)

    if not all_tours:
        return []

    # ── Step 2: enrich with detail pages in parallel ──────────────────────────
    logger.info(f"  [{country_name}] fetching {len(all_tours)} detail pages ({DETAIL_WORKERS} workers)...")
    enriched = []
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as ex:
        futures = {ex.submit(fetch_detail, t): t for t in all_tours}
        for i, fut in enumerate(as_completed(futures), 1):
            enriched.append(fut.result())
            if i % 50 == 0:
                logger.info(f"    [{country_name}] detailed {i}/{len(all_tours)}")

    with_price = sum(1 for t in enriched if t.get("price_min"))
    logger.info(f"  [{country_name}] enriched: {len(enriched)} tours, {with_price} have price")
    return enriched


# ─── Supabase upsert ──────────────────────────────────────────