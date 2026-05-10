#!/usr/bin/env python3
"""
TourFiremai Nightly Scraper v3 — with faimai (flash-sale) support
==================================================================
Scrapes all tour listings from tourfiremai.com:
  1. Normal tours from /intertour/<country_id>/   → source_type='normal'
  2. Flash-sale tours from /faimai                → source_type='faimai', is_faimai=true

Enriches each tour with:
  - price_min, airline, departure_dates  (from detail page)
  - discount_text, badge_text            (faimai only)

Run via GitHub Actions nightly at 02:00 AM BKK (19:00 UTC).

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
    "User-Agent": "Mozilla/5.0 (compatible; TourFiremaiBot/3.0; +https://tourfiremai.com)"
}

MAX_PAGES_PER_COUNTRY   = 25
DELAY_BETWEEN_PAGES     = 0.4
DELAY_BETWEEN_COUNTRIES = 1.0
DETAIL_WORKERS          = 5
UPSERT_CHUNK_SIZE       = 100

THAI_MONTHS     = r"(?:ม\.ค|ก\.พ|มี\.ค|เม\.ย|พ\.ค|มิ\.ย|ก\.ค|ส\.ค|ก\.ย|ต\.ค|พ\.ย|ธ\.ค)"
DATE_PATTERN    = re.compile(rf"\d{{1,2}}\s*{THAI_MONTHS}\.?\s*\d{{0,4}}")
PRICE_PATTERN   = re.compile(r"([\d,]{{4,}})\s*บาท")
AIRLINE_PATTERN = re.compile(
    r"\b(XJ|VZ|SL|FD|DD|TG|WE|PG|QR|TK|EK|MH|CX|XI|PR|OZ|KE|SQ|NH|JL|CZ|MU|CA|BR|VN|BX|Z2|3U|AQ|HX|BI)\b"
)

COUNTRY_MAP = {
    "1": "เกาหลี",        "2": "ญี่ปุ่น",     "3": "ฮ่องกง",
    "4": "สิงคโปร์",     "5": "จีน",          "6": "มาเลเซีย",
    "7": "เวียดนาม",     "8": "พม่า",          "9": "ลาว",
    "18": "อินโดนีเซีย", "19": "ไต้หวัน",    "29": "มาเก๊า",
    "104": "ฟิลิปปินส์",
    "14": "อินเดีย",    "20": "ภูฏาน",        "182": "ศรีลังกา",
    "72": "สหรัฐอาหรับฯ","70": "จอร์แดน",    "16": "อียิปต์",
    "42": "อังกฤษ",     "64": "สวิตเซอร์แลนด์","100": "เยอรมนี",
    "101": "ฝรั่งเศส",  "102": "อิตาลี",       "105": "สเปน",
    "159": "ออสเตรีย",  "169": "กรีซ",          "200": "โปรตุเกส",
    "47": "สแกนดิเนเวีย","168": "จอร์เจีย",   "71": "ตุรเคีย",
    "80": "ยุโรปตะวันออก",
    "10": "ออสเตรเลีย", "11": "นิวซีแลนด์",
    "12": "อเมริกา",    "73": "แคนาดา",
    "17": "รัสเซีย",    "256": "คาซัคสถาน",
}

# Country detection keywords for faimai tours
FAIMAI_COUNTRY_KEYWORDS = [
    (["ญี่ปุ่น","โตเกียว","โอซาก้า","ฮอกไกโด","ฟุกุโอกะ","นาโกย่า","คิวชู","นารา","เกียวโต"], "2", "ญี่ปุ่น"),
    (["เกาหลี","โซล","ปูซาน","เชจู","อินชอน"], "1", "เกาหลี"),
    (["เวียดนาม","ดานัง","ฮานอย","โฮจิมินห์","ฮอยอัน","ซาปา","ฮาลอง"], "7", "เวียดนาม"),
    (["จีน","เฉิงตู","คุนหมิง","ปักกิ่ง","เซี่ยงไฮ้","จางเจียเจี้ย","กวางโจว","จูไห่",
      "ฉงชิ่ง","กุ้ยหลิน","ซีอาน","ลี่เจียง","ต้าหลี่","เซินเจิ้น"], "5", "จีน"),
    (["ฮ่องกง"], "3", "ฮ่องกง"),
    (["มาเก๊า"], "29", "มาเก๊า"),
    (["ไต้หวัน","ไทเป","ไถจง"], "19", "ไต้หวัน"),
    (["สิงคโปร์"], "4", "สิงคโปร์"),
    (["มาเลเซีย","กัวลาลัมเปอร์"], "6", "มาเลเซีย"),
    (["พม่า","ย่างกุ้ง","มัณฑะเลย์"], "8", "พม่า"),
    (["อินโดนีเซีย","บาหลี","จาการ์ตา"], "18", "อินโดนีเซีย"),
    (["อินเดีย"], "14", "อินเดีย"),
    (["ดูไบ","UAE","อาหรับ"], "72", "สหรัฐอาหรับฯ"),
    (["ตุรกี","ตุรเคีย","อิสตันบูล"], "71", "ตุรเคีย"),
    (["จอร์เจีย","ทบิลิซิ"], "168", "จอร์เจีย"),
    (["อิตาลี","โรม","เวนิส","มิลาน"], "102", "อิตาลี"),
    (["ฝรั่งเศส","ปารีส"], "101", "ฝรั่งเศส"),
    (["สวิส","สวิตเซอร์แลนด์"], "64", "สวิตเซอร์แลนด์"),
    (["กรีซ","เอเธนส์","ซันโตรินี"], "169", "กรีซ"),
    (["อังกฤษ","ลอนดอน"], "42", "อังกฤษ"),
    (["ออสเตรเลีย","ซิดนีย์","เมลเบิร์น"], "10", "ออสเตรเลีย"),
    (["ฟิลิปปินส์","มะนิลา","เซบู"], "104", "ฟิลิปปินส์"),
    (["ลาว","เวียงจันทน์"], "9", "ลาว"),
    (["กัมพูชา","เสียมเรียบ","พนมเปญ"], "103", "กัมพูชา"),
]

PRIORITY_COUNTRY_IDS = ["2", "1", "7", "5", "3", "4", "19"]


def infer_country_from_name(name: str) -> tuple:
    """Return (country_id, country_name) inferred from tour name."""
    for keywords, cid, cname in FAIMAI_COUNTRY_KEYWORDS:
        for kw in keywords:
            if kw in name:
                return cid, cname
    return None, None


# ─── Detail page scraper ──────────────────────────────────────────────────────

def fetch_detail(tour: dict) -> dict:
    url = tour.get("url", "")
    if not url:
        return tour
    try:
        resp = requests.get(url, headers=WEB_HEADERS, timeout=15)
        resp.raise_for_status()
        text = resp.text

        prices = []
        for m in PRICE_PATTERN.finditer(text):
            try:
                val = int(m.group(1).replace(",", ""))
                if 5_000 <= val <= 500_000:
                    prices.append(val)
            except ValueError:
                pass
        tour["price_min"] = min(prices) if prices else None

        airlines = AIRLINE_PATTERN.findall(text)
        tour["airline"] = airlines[0] if airlines else None

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


# ─── Normal listing page scraper ─────────────────────────────────────────────

def parse_listing_cards(html_text: str, country_id: str, country_name: str) -> list:
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
            "source_type":     "normal",
            "is_faimai":       False,
            "last_scraped":    now_iso,
            "price_min":       None,
            "airline":         None,
            "departure_dates": None,
            "discount_text":   None,
        })
    return tours


def scrape_country(country_id: str, country_name: str) -> list:
    base_url = f"https://www.tourfiremai.com/intertour/{country_id}/"
    all_tours = []
    seen_codes = set()

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

    logger.info(f"  [{country_name}] enriching {len(all_tours)} tours...")
    enriched = []
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as ex:
        futures = {ex.submit(fetch_detail, t): t for t in all_tours}
        for i, fut in enumerate(as_completed(futures), 1):
            enriched.append(fut.result())
            if i % 50 == 0:
                logger.info(f"    [{country_name}] {i}/{len(all_tours)} done")

    with_price = sum(1 for t in enriched if t.get("price_min"))
    logger.info(f"  [{country_name}] done: {len(enriched)} tours, {with_price} have price")
    return enriched


# ─── Faimai (flash-sale) scraper ──────────────────────────────────────────────

def scrape_faimai() -> list:
    """Scrape https://www.tourfiremai.com/faimai and return tour dicts with is_faimai=True."""
    logger.info("Scraping /faimai page...")
    try:
        resp = requests.get("https://www.tourfiremai.com/faimai",
                            headers=WEB_HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"  faimai fetch error: {e}")
        return []

    now_iso = datetime.now(timezone.utc).isoformat()
    blocks = re.split(r'<div class="b-one-pg', resp.text)
    tours = []

    for block in blocks[1:]:
        url_m    = re.search(r'href="(https://www\.tourfiremai\.com/tour/[^"]+)"', block)
        name_m   = re.search(r'<h3>(.*?)</h3>', block, re.DOTALL)
        days_m   = re.search(r'(\d+)วัน', block)
        disc_m   = re.search(r'nb-dcprice-pgt">(.*?)<span>', block, re.DOTALL)
        badge_m  = re.search(r'(?:badge|label|tag)[^>]*>(.*?)<', block, re.DOTALL)

        if not url_m or not name_m:
            continue

        url = url_m.group(1)
        tc_m = re.search(r'/tour/(ap\w+)', url)
        if not tc_m:
            continue
        tour_code = tc_m.group(1)

        name     = re.sub(r'<[^>]+>', '', name_m.group(1)).strip()
        days     = days_m.group(1) if days_m else None
        disc     = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', disc_m.group(1))).strip() if disc_m else None
        badge    = re.sub(r'<[^>]+>', '', badge_m.group(1)).strip() if badge_m else None

        country_id, country_name = infer_country_from_name(name)

        tour = {
            "tour_code":       tour_code,
            "name":            name,
            "url":             url,
            "country_id":      country_id,
            "country_name":    country_name,
            "source_type":     "faimai",
            "is_faimai":       True,
            "discount_text":   disc,
            "badge_text":      badge,
            "last_scraped":    now_iso,
            "price_min":       None,
            "airline":         None,
            "departure_dates": None,
        }
        tours.append(tour)

    logger.info(f"  /faimai: found {len(tours)} tours — enriching detail pages...")

    # Enrich with detail pages
    enriched = []
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as ex:
        futures = {ex.submit(fetch_detail, t): t for t in tours}
        for fut in as_completed(futures):
            enriched.append(fut.result())

    with_price = sum(1 for t in enriched if t.get("price_min"))
    logger.info(f"  /faimai: enriched {len(enriched)} tours, {with_price} have price")
    return enriched


# ─── Supabase upsert ──────────────────────────────────────────────────────────

def upsert_tours(tours: list) -> int:
    """Upsert tour records to Supabase. Returns count of upserted records."""
    if not tours:
        return 0

    total = 0
    for i in range(0, len(tours), UPSERT_CHUNK_SIZE):
        chunk = tours[i:i + UPSERT_CHUNK_SIZE]
        try:
            resp = requests.post(
                f"{SUPABASE_URL}/rest/v1/tours",
                json=chunk,
                headers=SUPABASE_HEADERS,
                timeout=30,
            )
            if resp.status_code in (200, 201):
                total += len(chunk)
                logger.info(f"  Upserted chunk {i//UPSERT_CHUNK_SIZE + 1}: {len(chunk)} records")
            else:
                logger.error(f"  Upsert error {resp.status_code}: {resp.text[:300]}")
        except Exception as e:
            logger.error(f"  Upsert exception: {e}")
    return total


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("TourFiremai Nightly Scraper v3 — with faimai support")
    logger.info("=" * 60)

    total_upserted = 0

    # ── 1. Scrape /faimai first (fast, ~12 tours) ─────────────────────────────
    logger.info("\n📍 Step 1: Scraping /faimai (flash-sale tours)")
    faimai_tours = scrape_faimai()
    if faimai_tours:
        n = upsert_tours(faimai_tours)
        total_upserted += n
        logger.info(f"  ✅ Faimai: {n} tours upserted")
    else:
        logger.warning("  ⚠️  Faimai: no tours found")

    # ── 2. Scrape normal tours by country ─────────────────────────────────────
    logger.info("\n📍 Step 2: Scraping normal tours by country")

    # Priority countries first
    order = PRIORITY_COUNTRY_IDS + [cid for cid in COUNTRY_MAP if cid not in PRIORITY_COUNTRY_IDS]

    for country_id in order:
        country_name = COUNTRY_MAP.get(country_id, f"country_{country_id}")
        logger.info(f"\n[{country_name}] (id={country_id})")
        try:
            tours = scrape_country(country_id, country_name)
            if tours:
                n = upsert_tours(tours)
                total_upserted += n
        except Exception as e:
            logger.error(f"  [{country_name}] scrape failed: {e}")
        time.sleep(DELAY_BETWEEN_COUNTRIES)

    logger.info(f"\n✅ Scraper done — total upserted: {total_upserted}")


if __name__ == "__main__":
    main()
