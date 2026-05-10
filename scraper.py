#!/usr/bin/env python3
"""
TourFiremai Scraper v4
======================
Modes:
  --mode faimai  → scrape /faimai only (4x daily via GitHub Actions)
  --mode normal  → scrape all /intertour/<country_id>/ (nightly)
  --mode all     → faimai first, then normal

Features:
  - Faimai stale-data handling: mark old is_faimai=true as is_active=false before each run
  - Discount parsing: original_price, promo_price, discount_amount, discount_percent
  - Fee extraction: tip_fee, visa_fee, visa_status, single_supplement
  - scrape_runs logging table
  - CLI --mode flag

Required env vars:
  SUPABASE_URL  — e.g. https://xxxx.supabase.co
  SUPABASE_KEY  — service_role key (write access)
"""

import argparse
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

SUPABASE_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}
SUPABASE_UPSERT_HEADERS = {**SUPABASE_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"}
SUPABASE_PATCH_HEADERS  = {**SUPABASE_HEADERS, "Prefer": "return=minimal"}

WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TourFiremaiBot/4.0; +https://tourfiremai.com)"
}

MAX_PAGES_PER_COUNTRY   = 25
DELAY_BETWEEN_PAGES     = 0.4
DELAY_BETWEEN_COUNTRIES = 1.0
DETAIL_WORKERS          = 5
UPSERT_CHUNK_SIZE       = 100

# ─── Regex patterns ───────────────────────────────────────────────────────────

THAI_MONTHS     = r"(?:ม\.ค|ก\.พ|มี\.ค|เม\.ย|พ\.ค|มิ\.ย|ก\.ค|ส\.ค|ก\.ย|ต\.ค|พ\.ย|ธ\.ค)"
DATE_PATTERN    = re.compile(rf"\d{{1,2}}\s*{THAI_MONTHS}\.?\s*\d{{0,4}}")
PRICE_PATTERN   = re.compile(r"([\d,]{4,})\s*บาท")
AIRLINE_PATTERN = re.compile(
    r"\b(XJ|VZ|SL|FD|DD|TG|WE|PG|QR|TK|EK|MH|CX|XI|PR|OZ|KE|SQ|NH|JL|CZ|MU|CA|BR|VN|BX|Z2|3U|AQ|HX|BI)\b"
)

# Fee patterns
TIP_PATTERN    = re.compile(
    r"(?:ทิป(?:ไกด์)?|tip(?:\s*guide)?)[^\d]{0,30}([\d,]{3,6})\s*(?:บาท|THB|USD)",
    re.IGNORECASE,
)
VISA_FREE_PAT  = re.compile(r"ไม่(?:ต้อง)?วีซ่า|VISA\s*FREE|ฟรีวีซ่า|ไม่ใช้วีซ่า", re.IGNORECASE)
VISA_FEE_PAT   = re.compile(
    r"(?:ค่า)?วีซ่า[^\d]{0,20}([\d,]{3,5})\s*(?:บาท|THB)",
    re.IGNORECASE,
)
SINGLE_SUP_PAT = re.compile(
    r"(?:พักเดี่ยว|single\s*supplement)[^\d]{0,20}([\d,]{3,6})\s*(?:บาท|THB)",
    re.IGNORECASE,
)

# Discount patterns (on /faimai listing page blocks)
ORIG_PRICE_PAT  = re.compile(r"(?:ราคาปกติ|ราคาเดิม|เดิม)[^\d]{0,10}([\d,]{4,7})", re.IGNORECASE)
PROMO_PRICE_PAT = re.compile(r"(?:ราคาโปร|ราคาพิเศษ|จากราคา)[^\d]{0,10}([\d,]{4,7})", re.IGNORECASE)
DISC_TEXT_PAT   = re.compile(r"ลด\s*([\d,]+)\s*บาท", re.IGNORECASE)
STRIKETHROUGH_PAT = re.compile(r"<(?:del|s|strike)[^>]*>([\d,\s]+บาท[^<]*)<", re.IGNORECASE)

# ─── Country maps ─────────────────────────────────────────────────────────────

COUNTRY_MAP = {
    "1": "เกาหลี",     "2": "ญี่ปุ่น",     "3": "ฮ่องกง",
    "4": "สิงคโปร์",  "5": "จีน",          "6": "มาเลเซีย",
    "7": "เวียดนาม",  "8": "พม่า",          "9": "ลาว",
    "18": "อินโดนีเซีย","19": "ไต้หวัน",  "29": "มาเก๊า",
    "104": "ฟิลิปปินส์","14": "อินเดีย",  "20": "ภูฏาน",
    "182": "ศรีลังกา","72": "สหรัฐอาหรับฯ","70": "จอร์แดน",
    "16": "อียิปต์",  "42": "อังกฤษ",      "64": "สวิตเซอร์แลนด์",
    "100": "เยอรมนี", "101": "ฝรั่งเศส",   "102": "อิตาลี",
    "105": "สเปน",    "159": "ออสเตรีย",   "169": "กรีซ",
    "200": "โปรตุเกส","47": "สแกนดิเนเวีย","168": "จอร์เจีย",
    "71": "ตุรเคีย",  "80": "ยุโรปตะวันออก",
    "10": "ออสเตรเลีย","11": "นิวซีแลนด์", "12": "อเมริกา",
    "73": "แคนาดา",   "17": "รัสเซีย",     "256": "คาซัคสถาน",
}

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


# ─── Supabase helpers ─────────────────────────────────────────────────────────

def supabase_patch(table: str, filters: dict, data: dict) -> bool:
    """PATCH (update) rows matching filters."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    params = {k: v for k, v in filters.items()}
    try:
        resp = requests.patch(
            f"{SUPABASE_URL}/rest/v1/{table}",
            params=params,
            json=data,
            headers=SUPABASE_PATCH_HEADERS,
            timeout=20,
        )
        return resp.status_code in (200, 204)
    except Exception as e:
        logger.error(f"supabase_patch error: {e}")
        return False


def supabase_upsert(table: str, rows: list) -> int:
    """Upsert rows in chunks. Returns count of upserted records."""
    if not rows:
        return 0
    total = 0
    for i in range(0, len(rows), UPSERT_CHUNK_SIZE):
        chunk = rows[i:i + UPSERT_CHUNK_SIZE]
        try:
            resp = requests.post(
                f"{SUPABASE_URL}/rest/v1/{table}",
                json=chunk,
                headers=SUPABASE_UPSERT_HEADERS,
                timeout=30,
            )
            if resp.status_code in (200, 201, 204):
                total += len(chunk)
                logger.info(f"  Upserted chunk {i // UPSERT_CHUNK_SIZE + 1}: {len(chunk)} rows")
            else:
                logger.error(f"  Upsert error {resp.status_code}: {resp.text[:300]}")
        except Exception as e:
            logger.error(f"  Upsert exception: {e}")
    return total


# ─── scrape_runs logging ──────────────────────────────────────────────────────

def log_scrape_start(scraper_type: str) -> int:
    """Insert scrape_runs row. Returns run_id (0 on failure)."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return 0
    try:
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/scrape_runs",
            json={"scraper_type": scraper_type, "status": "running"},
            headers={**SUPABASE_HEADERS, "Prefer": "return=representation"},
            timeout=10,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            run_id = data[0]["id"] if data else 0
            logger.info(f"scrape_run #{run_id} started ({scraper_type})")
            return run_id
    except Exception as e:
        logger.warning(f"log_scrape_start error: {e}")
    return 0


def log_scrape_end(run_id: int, total_found: int, total_upserted: int,
                   total_failed: int, status: str, error_msg: str = None):
    """Update scrape_runs row with results."""
    if not run_id or not SUPABASE_URL:
        return
    try:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/scrape_runs",
            params={"id": f"eq.{run_id}"},
            json={
                "finished_at":    datetime.now(timezone.utc).isoformat(),
                "total_found":    total_found,
                "total_upserted": total_upserted,
                "total_failed":   total_failed,
                "status":         status,
                "error_message":  error_msg,
            },
            headers=SUPABASE_PATCH_HEADERS,
            timeout=10,
        )
        logger.info(
            f"scrape_run #{run_id} done — found={total_found} upserted={total_upserted} "
            f"failed={total_failed} status={status}"
        )
    except Exception as e:
        logger.warning(f"log_scrape_end error: {e}")


# ─── Country helper ───────────────────────────────────────────────────────────

def infer_country_from_name(name: str) -> tuple:
    """Return (country_id, country_name) inferred from tour name keywords."""
    for keywords, cid, cname in FAIMAI_COUNTRY_KEYWORDS:
        for kw in keywords:
            if kw in name:
                return cid, cname
    return None, None


# ─── Fee extraction ───────────────────────────────────────────────────────────

def _parse_int(s: str) -> int:
    """Convert '3,500' → 3500. Returns 0 on failure."""
    try:
        return int(s.replace(",", "").strip())
    except Exception:
        return 0


def extract_fee_details(html_text: str) -> dict:
    """
    Extract tip_fee, visa_fee, visa_status, single_supplement from detail page HTML.
    Returns dict with available keys only (no None padding).
    """
    fees = {}
    # Remove HTML tags for easier regex matching
    plain = re.sub(r"<[^>]+>", " ", html_text)
    plain = re.sub(r"\s+", " ", plain)

    # ── tip ──────────────────────────────────────────────────────────────────
    m = TIP_PATTERN.search(plain)
    if m:
        v = _parse_int(m.group(1))
        if 100 <= v <= 50000:
            fees["tip_fee"] = v

    # ── visa free ─────────────────────────────────────────────────────────────
    if VISA_FREE_PAT.search(plain):
        fees["visa_status"] = "VISA FREE"
        fees["visa_fee"] = 0
    else:
        m = VISA_FEE_PAT.search(plain)
        if m:
            v = _parse_int(m.group(1))
            if 100 <= v <= 20000:
                fees["visa_fee"] = v
                fees["visa_status"] = f"{v:,} บาท"

    # ── single supplement ─────────────────────────────────────────────────────
    m = SINGLE_SUP_PAT.search(plain)
    if m:
        v = _parse_int(m.group(1))
        if 500 <= v <= 100000:
            fees["single_supplement"] = v

    # ── estimated total ───────────────────────────────────────────────────────
    # Stored per-tour, not computed here — app.py computes it at response time
    if fees:
        fees["fee_detail_status"]     = "extracted"
        fees["fee_detail_updated_at"] = datetime.now(timezone.utc).isoformat()
    else:
        fees["fee_detail_status"] = "not_found"

    return fees


# ─── Discount parsing (from /faimai listing block) ───────────────────────────

def parse_discount_from_block(block_html: str) -> dict:
    """
    Parse discount info from a /faimai listing card block.
    Returns dict with: original_price, promo_price, discount_amount,
                       discount_percent, discount_text, promo_badge
    Only populated fields are returned.
    """
    info = {}
    plain = re.sub(r"<[^>]+>", " ", block_html)
    plain = re.sub(r"\s+", " ", plain)

    # ── Try strikethrough price (del/s tags) = original price ────────────────
    m = STRIKETHROUGH_PAT.search(block_html)
    if m:
        raw = re.sub(r"[^\d,]", "", m.group(1))
        v = _parse_int(raw)
        if 5000 <= v <= 500000:
            info["original_price"] = v

    # ── ราคาปกติ / ราคาเดิม ──────────────────────────────────────────────────
    if "original_price" not in info:
        m = ORIG_PRICE_PAT.search(plain)
        if m:
            v = _parse_int(m.group(1))
            if 5000 <= v <= 500000:
                info["original_price"] = v

    # ── ราคาโปร ──────────────────────────────────────────────────────────────
    m = PROMO_PRICE_PAT.search(plain)
    if m:
        v = _parse_int(m.group(1))
        if 3000 <= v <= 500000:
            info["promo_price"] = v

    # ── ลด X บาท ─────────────────────────────────────────────────────────────
    m = DISC_TEXT_PAT.search(plain)
    if m:
        info["discount_text"] = m.group(0).strip()
        v = _parse_int(m.group(1))
        if v > 0:
            info["discount_amount"] = v

    # ── Compute missing values ────────────────────────────────────────────────
    orig  = info.get("original_price")
    promo = info.get("promo_price")
    disc  = info.get("discount_amount")

    if orig and promo and not disc:
        d = orig - promo
        if d > 0:
            info["discount_amount"] = d

    if orig and not promo and disc:
        info["promo_price"] = orig - disc

    if orig and promo and orig > promo:
        pct = round((orig - promo) / orig * 100, 1)
        info["discount_percent"] = pct

    return info


# ─── Detail page enrichment ───────────────────────────────────────────────────

def fetch_detail(tour: dict, extract_fees: bool = False) -> dict:
    """Fetch detail page; populate price_min, airline, departure_dates (+ fees if extract_fees)."""
    url = tour.get("url", "")
    if not url:
        return tour
    try:
        resp = requests.get(url, headers=WEB_HEADERS, timeout=15)
        resp.raise_for_status()
        text = resp.text

        # Price
        prices = [
            int(m.group(1).replace(",", ""))
            for m in PRICE_PATTERN.finditer(text)
            if 5_000 <= int(m.group(1).replace(",", "")) <= 500_000
        ]
        if prices:
            tour["price_min"] = min(prices)

        # Airline
        airlines = AIRLINE_PATTERN.findall(text)
        if airlines:
            tour["airline"] = airlines[0]

        # Dates
        raw_dates = DATE_PATTERN.findall(text)
        seen, unique = set(), []
        for d in raw_dates:
            d = d.strip()
            if d not in seen:
                seen.add(d)
                unique.append(d)
        if unique:
            tour["departure_dates"] = ", ".join(unique[:12])

        # tour_code_real — from tcode= param in booking URL
        tcode_m = re.search(r'tcode=([A-Z0-9\-]+)', text)
        if tcode_m:
            tour["tour_code_real"] = tcode_m.group(1).strip()
        else:
            # fallback: label รหัสทัวร์ followed by txt-pd-l value
            tc_label_m = re.search(
                r'รหัสทัวร์[^<]{0,60}<p[^>]*class="txt-pd-l"[^>]*>([^<]+)</p>',
                text
            )
            if tc_label_m:
                tour["tour_code_real"] = tc_label_m.group(1).strip()

        # Fees (only for faimai tours — saves time on normal scrape)
        if extract_fees:
            fees = extract_fee_details(text)
            tour.update(fees)

    except Exception as e:
        logger.debug(f"  detail error {url}: {e}")
    return tour


# ─── Faimai scraper ───────────────────────────────────────────────────────────

def mark_faimai_inactive():
    """Set is_active=false for all currently-active faimai tours (stale data handling)."""
    logger.info("  Marking existing faimai tours as inactive...")
    ok = supabase_patch(
        "tours",
        {"is_faimai": "eq.true", "is_active": "eq.true"},
        {"is_active": False},
    )
    if ok:
        logger.info("  ✅ Faimai tours marked inactive")
    else:
        logger.warning("  ⚠️  Could not mark faimai tours inactive (Supabase error)")


def scrape_faimai_page() -> list:
    """Scrape /faimai listing page. Returns raw tour dicts (not yet enriched)."""
    logger.info("  Fetching https://www.tourfiremai.com/faimai ...")
    try:
        resp = requests.get("https://www.tourfiremai.com/faimai",
                            headers=WEB_HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"  faimai fetch error: {e}")
        return []

    now_iso = datetime.now(timezone.utc).isoformat()
    blocks = re.split(r'(?=<div class="b-one-pg)', resp.text)
    tours = []

    for block in blocks:
        # URL / tour_code
        url_m = re.search(r'href="(https://www\.tourfiremai\.com/tour/[^"]+)"', block)
        if not url_m:
            url_m = re.search(r'href="(/tour/[^"]+)"', block)
            if not url_m:
                continue
            url = "https://www.tourfiremai.com" + url_m.group(1)
        else:
            url = url_m.group(1)

        tc_m = re.search(r"/tour/(ap\w+)", url)
        if not tc_m:
            continue
        tour_code = tc_m.group(1)  # web_code (ap...)

        # Name
        name_m = re.search(r"<h3[^>]*>(.*?)</h3>", block, re.DOTALL)
        if not name_m:
            continue
        name = re.sub(r"<[^>]+>", "", name_m.group(1)).strip()

        # Days
        days_m = re.search(r"(\d+)\s*วัน", block)
        days = days_m.group(1) if days_m else None

        # Discount info from listing block
        disc_info = parse_discount_from_block(block)

        # Badge
        badge_m = re.search(r'(?:badge|label|tag|flag)[^>]*>(.*?)<', block, re.DOTALL | re.IGNORECASE)
        badge = re.sub(r"<[^>]+>", "", badge_m.group(1)).strip() if badge_m else None
        # Fallback badge from discount_text if no badge found
        if not badge and disc_info.get("discount_text"):
            badge = "ไฟไหม้"

        # Country inference
        country_id, country_name = infer_country_from_name(name)

        tour = {
            "web_code":       tour_code,
            "tour_code":      tour_code,   # kept for backward compat (upsert key)
            "tour_code_real": None,         # populated by fetch_detail
            "name":           name,
            "url":            url,
            "country_id":     country_id,
            "country_name":   country_name,
            "source_url":     "https://www.tourfiremai.com/faimai",
            "source_type":    "faimai",
            "is_faimai":      True,
            "is_active":      True,
            "last_seen_at":   now_iso,
            "last_scraped":   now_iso,
            "price_min":      None,
            "promo_price":    disc_info.get("promo_price"),
            "original_price": disc_info.get("original_price"),
            "discount_amount":  disc_info.get("discount_amount"),
            "discount_percent": disc_info.get("discount_percent"),
            "discount_text":  disc_info.get("discount_text"),
            "promo_badge":    badge,
            "airline":        None,
            "departure_dates": None,
        }
        tours.append(tour)

    logger.info(f"  /faimai listing: found {len(tours)} tours")
    return tours


def scrape_faimai_tours() -> dict:
    """
    Full faimai scrape:
      1. Mark existing faimai tours inactive
      2. Scrape /faimai listing
      3. Enrich with detail pages (price + fees)
      4. Upsert to Supabase
    Returns stats dict.
    """
    run_id = log_scrape_start("faimai")
    started_at = datetime.now(timezone.utc)
    total_found = total_upserted = total_failed = 0

    try:
        # Step 1: Stale marking
        mark_faimai_inactive()

        # Step 2: Scrape listing
        tours = scrape_faimai_page()
        total_found = len(tours)

        if not tours:
            logger.warning("  No faimai tours found — scrape_runs logged as warning")
            log_scrape_end(run_id, 0, 0, 0, "warning", "No tours found on /faimai")
            return {"found": 0, "upserted": 0, "failed": 0}

        # Step 3: Enrich detail pages (price + fees)
        logger.info(f"  Enriching {len(tours)} faimai tours (price + fees)...")
        enriched = []
        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as ex:
            futures = {ex.submit(fetch_detail, t, True): t for t in tours}
            for fut in as_completed(futures):
                try:
                    enriched.append(fut.result())
                except Exception as e:
                    logger.error(f"  Detail error: {e}")
                    total_failed += 1

        # Set promo_price = price_min if we got a price but no promo_price
        for t in enriched:
            if t.get("price_min") and not t.get("promo_price"):
                t["promo_price"] = t["price_min"]
            # price_min should always be the current (promo) price
            if t.get("promo_price") and not t.get("price_min"):
                t["price_min"] = t["promo_price"]

        with_price = sum(1 for t in enriched if t.get("price_min"))
        with_fees  = sum(1 for t in enriched if t.get("tip_fee"))
        with_disc  = sum(1 for t in enriched if t.get("discount_amount"))
        logger.info(
            f"  /faimai enriched: {len(enriched)} tours, "
            f"{with_price} have price, {with_fees} have fees, {with_disc} have discount"
        )

        # Step 4: Upsert
        total_upserted = supabase_upsert("tours", enriched)
        elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
        logger.info(f"  ✅ Faimai done in {elapsed:.1f}s — upserted {total_upserted}")
        log_scrape_end(run_id, total_found, total_upserted, total_failed, "success")

    except Exception as e:
        logger.error(f"  Faimai scrape failed: {e}")
        log_scrape_end(run_id, total_found, total_upserted, total_failed, "error", str(e))
        raise

    return {"found": total_found, "upserted": total_upserted, "failed": total_failed}


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
            "web_code":       tour_code,
            "tour_code":      tour_code,   # kept for backward compat (upsert key)
            "tour_code_real": None,         # populated by fetch_detail
            "name":           name,
            "url":            url,
            "country_id":     country_id,
            "country_name":   country_name,
            "source_type":    "normal",
            "is_faimai":      False,
            "is_active":      True,
            "last_scraped":   now_iso,
            "price_min":      None,
            "airline":        None,
            "departure_dates": None,
            "discount_text":  None,
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
        futures = {ex.submit(fetch_detail, t, False): t for t in all_tours}
        for i, fut in enumerate(as_completed(futures), 1):
            enriched.append(fut.result())
            if i % 50 == 0:
                logger.info(f"    [{country_name}] {i}/{len(all_tours)} done")

    with_price = sum(1 for t in enriched if t.get("price_min"))
    logger.info(f"  [{country_name}] done: {len(enriched)} tours, {with_price} have price")
    return enriched


def scrape_all_normal_tours() -> dict:
    """Scrape all /intertour/<country_id>/ pages and upsert to Supabase."""
    run_id = log_scrape_start("normal")
    started_at = datetime.now(timezone.utc)
    total_found = total_upserted = total_failed = 0

    try:
        order = PRIORITY_COUNTRY_IDS + [cid for cid in COUNTRY_MAP if cid not in PRIORITY_COUNTRY_IDS]
        for country_id in order:
            country_name = COUNTRY_MAP.get(country_id, f"country_{country_id}")
            logger.info(f"\n[{country_name}] (id={country_id})")
            try:
                tours = scrape_country(country_id, country_name)
                total_found += len(tours)
                if tours:
                    n = supabase_upsert("tours", tours)
                    total_upserted += n
            except Exception as e:
                logger.error(f"  [{country_name}] scrape failed: {e}")
                total_failed += 1
            time.sleep(DELAY_BETWEEN_COUNTRIES)

        elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
        logger.info(f"\n✅ Normal scrape done in {elapsed:.0f}s — found={total_found} upserted={total_upserted}")
        log_scrape_end(run_id, total_found, total_upserted, total_failed, "success")

    except Exception as e:
        logger.error(f"Normal scrape failed: {e}")
        log_scrape_end(run_id, total_found, total_upserted, total_failed, "error", str(e))
        raise

    return {"found": total_found, "upserted": total_upserted, "failed": total_failed}


# ─── CLI entry point ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="TourFiremai Scraper v4")
    parser.add_argument(
        "--mode",
        choices=["normal", "faimai", "all"],
        default="normal",
        help="Scrape mode: normal (nightly), faimai (4x daily), all (both)",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info(f"TourFiremai Scraper v4 — mode={args.mode}")
    logger.info("=" * 60)

    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("Missing SUPABASE_URL or SUPABASE_KEY environment variables")
        raise SystemExit(1)

    if args.mode == "faimai":
        stats = scrape_faimai_tours()
        logger.info(f"\n📊 Faimai — found={stats['found']} upserted={stats['upserted']} failed={stats['failed']}")

    elif args.mode == "normal":
        stats = scrape_all_normal_tours()
        logger.info(f"\n📊 Normal — found={stats['found']} upserted={stats['upserted']} failed={stats['failed']}")

    elif args.mode == "all":
        logger.info("\n📍 Step 1: Faimai scrape")
        f_stats = scrape_faimai_tours()
        logger.info("\n📍 Step 2: Normal scrape")
        n_stats = scrape_all_normal_tours()
        logger.info(
            f"\n📊 All done — faimai: {f_stats['upserted']} upserted | "
            f"normal: {n_stats['upserted']} upserted"
        )


if __name__ == "__main__":
    main()
