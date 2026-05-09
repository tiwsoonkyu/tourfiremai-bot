#!/usr/bin/env python3
"""
TourFiremai Nightly Scraper
===========================
Scrapes all tour listings from tourfiremai.com and upserts into the
Supabase `tours` table.

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
from datetime import datetime, timezone

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
    "Prefer": "resolution=merge-duplicates",   # upsert on UNIQUE constraint
}

WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TourFiremaiBot/1.0; +https://tourfiremai.com)"
}

MAX_PAGES_PER_COUNTRY = 25   # safety cap (Japan has ~16 pages)
DELAY_BETWEEN_PAGES   = 0.5  # seconds — be polite to the server
DELAY_BETWEEN_COUNTRIES = 1.0
UPSERT_CHUNK_SIZE     = 200

# All countries (same as app.py COUNTRY_MAP)
COUNTRY_MAP = {
    "1": "เกาหลี",        "2": "ญี่ปุ่น",     "3": "ฮ่องกง",
    "4": "สิงคโปร์",     "5": "จีน",          "6": "มาเลเซีย",
    "7": "เวียดนาม",     "8": "พม่า",          "9": "ลาว",
    "18": "อินโดนีเซีย", "19": "ไต้หวัน",    "29": "มาเก๊า",
    "104": "ฟิลิปปินส์",
    "14": "อินเดีย",    "20": "ภูฏาน",        "182": "ศรีลังกา",
    "184": "ทิเบต",     "173": "อุซเบกิสถาน", "256": "คาซัคสถาน",
    "164": "ปากีสถาน",  "165": "มองโกเลีย",
    "72": "สหรัฐอาหรับฯ", "70": "จอร์แดน",  "16": "อียิปต์",
    "167": "เคนย่า",    "68": "แอฟริกาใต้",   "161": "โมร็อกโก",
    "183": "อิหร่าน",
    "42": "อังกฤษ",     "64": "สวิตเซอร์แลนด์", "100": "เยอรมนี",
    "101": "ฝรั่งเศส",  "102": "อิตาลี",       "105": "สเปน",
    "159": "ออสเตรีย",  "169": "กรีซ",          "200": "โปรตุเกส",
    "213": "เบลเยี่ยม", "308": "เนเธอร์แลนด์", "2217": "เบเนลักซ์",
    "47": "สแกนดิเนเวีย", "65": "ฟินแลนด์",   "153": "สวีเดน",
    "162": "นอร์เวย์",  "232": "เดนมาร์ก",     "25": "ไอซ์แลนด์",
    "194": "ไอร์แลนด์", "197": "สกอตแลนด์",
    "80": "ยุโรปตะวันออก", "66": "โครเอเชีย", "166": "โปแลนด์",
    "168": "จอร์เจีย",  "71": "ตุรเคีย",       "2213": "บอลติก",
    "2220": "โรมาเนีย", "275": "มอลตา",        "276": "มอนเตเนโกร",
    "10": "ออสเตรเลีย", "11": "นิวซีแลนด์",
    "12": "อเมริกา",    "73": "แคนาดา",         "174": "บราซิล",
    "175": "อาร์เจนติน่า", "226": "โคลอมเบีย", "272": "เม็กซิโก",
    "17": "รัสเซีย",
}

# Scrape top countries first (most bookings per CLAUDE.md)
PRIORITY_COUNTRY_IDS = ["2", "1", "7", "5", "3", "4", "19"]  # Japan, Korea, Vietnam, China, HK, SG, Taiwan


# ─── Scraping ─────────────────────────────────────────────────────────────────

def parse_listing_cards(html_text: str, country_id: str, country_name: str) -> list[dict]:
    """Extract tour cards from one listing page."""
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
            "tour_code":    tour_code,
            "name":         name,
            "url":          url,
            "country_id":   country_id,
            "country_name": country_name,
            "last_scraped": now_iso,
        })

    return tours


def scrape_country(country_id: str, country_name: str) -> list[dict]:
    """Scrape all listing pages for one country."""
    base_url = f"https://www.tourfiremai.com/intertour/{country_id}/"
    all_tours: list[dict] = []
    seen_codes: set[str] = set()

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

        # Deduplicate (sometimes last page repeats)
        new_cards = [c for c in cards if c["tour_code"] not in seen_codes]
        seen_codes.update(c["tour_code"] for c in new_cards)
        all_tours.extend(new_cards)

        logger.info(f"  [{country_name}] page {page_num}: +{len(new_cards)} tours (total {len(all_tours)})")

        # If we got fewer than 6 cards, we're on the last page
        if len(cards) < 6:
            break

        time.sleep(DELAY_BETWEEN_PAGES)

    return all_tours


# ─── Supabase upsert ──────────────────────────────────────────────────────────

def upsert_tours(tours: list[dict]) -> int:
    """Upsert a batch of tours into Supabase. Returns count successfully sent."""
    if not tours:
        return 0
    endpoint = f"{SUPABASE_URL}/rest/v1/tours"
    total_ok = 0

    for i in range(0, len(tours), UPSERT_CHUNK_SIZE):
        chunk = tours[i : i + UPSERT_CHUNK_SIZE]
        try:
            resp = requests.post(
                endpoint,
                json=chunk,
                headers=SUPABASE_HEADERS,
                timeout=30,
            )
            if resp.status_code in (200, 201):
                total_ok += len(chunk)
                logger.info(f"  upserted chunk {i//UPSERT_CHUNK_SIZE + 1}: {len(chunk)} rows OK")
            else:
                logger.error(f"  upsert error {resp.status_code}: {resp.text[:400]}")
        except Exception as e:
            logger.error(f"  upsert exception: {e}")

    return total_ok


def get_db_tour_count() -> int:
    """Return total rows in tours table (for logging)."""
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/tours",
            params={"select": "tour_code"},
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Prefer": "count=exact",
                "Range": "0-0",
            },
            timeout=10,
        )
        content_range = resp.headers.get("Content-Range", "")
        m = re.search(r"/(\d+)$", content_range)
        return int(m.group(1)) if m else -1
    except Exception:
        return -1


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("TourFiremai Nightly Scraper — START")
    logger.info(f"Supabase: {SUPABASE_URL}")
    logger.info("=" * 60)

    start_time = time.time()
    grand_total_scraped = 0
    grand_total_upserted = 0

    # Ordered country IDs (priority first)
    other_ids = [cid for cid in COUNTRY_MAP if cid not in PRIORITY_COUNTRY_IDS]
    ordered_ids = PRIORITY_COUNTRY_IDS + other_ids

    for country_id in ordered_ids:
        country_name = COUNTRY_MAP[country_id]
        logger.info(f"\n▶ {country_name} (id={country_id})")

        tours = scrape_country(country_id, country_name)
        grand_total_scraped += len(tours)

        if tours:
            upserted = upsert_tours(tours)
            grand_total_upserted += upserted
            logger.info(f"  ✅ {upserted}/{len(tours)} upserted for {country_name}")
        else:
            logger.info(f"  ⚠️  no tours found for {country_name}")

        time.sleep(DELAY_BETWEEN_COUNTRIES)

    elapsed = time.time() - start_time
    db_total = get_db_tour_count()

    logger.info("\n" + "=" * 60)
    logger.info(f"DONE — scraped: {grand_total_scraped}, upserted: {grand_total_upserted}")
    logger.info(f"DB total rows now: {db_total}")
    logger.info(f"Elapsed: {elapsed:.0f}s ({elapsed/60:.1f} min)")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
