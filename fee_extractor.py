#!/usr/bin/env python3
"""
fee_extractor.py - PDF Fee Extractor for TourFireMai Bot
=========================================================
Extracts tip_fee, visa_fee, single_supplement, deposit from tour PDFs.
Uses PyMuPDF text extraction + Claude Vision OCR fallback.

Usage:
  python fee_extractor.py                     # process NULL-status tours
  FORCE_RECHECK=true python fee_extractor.py  # reprocess all active tours
  FEE_BATCH_SIZE=100 python fee_extractor.py  # larger batch
"""

import os, re, json, time, base64, logging, sys
import requests
from datetime import datetime, timezone

try:
    import fitz
except ImportError:
    sys.exit("PyMuPDF not found. Run: pip install pymupdf")

try:
    from anthropic import Anthropic
except ImportError:
    sys.exit("anthropic not found. Run: pip install anthropic")

# ── Config ────────────────────────────────────────────────────────────────────
SUPABASE_URL      = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY      = os.environ.get("SUPABASE_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
BATCH_SIZE        = int(os.getenv("FEE_BATCH_SIZE", "50"))
FORCE_RECHECK     = os.getenv("FORCE_RECHECK", "false").lower() == "true"
PDF_BASE_URL      = "https://www.tourfiremai.com/programtour/tour_{}.pdf"
MIN_TEXT_CHARS    = 80
REQUEST_DELAY     = 0.5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

claude = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

# ── Supabase helpers ──────────────────────────────────────────────────────────
def _sb_headers():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=minimal",
    }

def sb_get(table, params):
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=_sb_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()

def sb_patch(table, match_params, data):
    resp = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=_sb_headers(),
        params=match_params,
        json=data,
        timeout=30,
    )
    resp.raise_for_status()

# ── PDF download ──────────────────────────────────────────────────────────────
_PDF_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TourFiremaiBot/1.0)"}

def download_pdf(pdf_url):
    try:
        resp = requests.get(pdf_url, headers=_PDF_HEADERS, timeout=30)
        if resp.status_code == 404:
            return None, "not_found"
        resp.raise_for_status()
        return resp.content, "ok"
    except Exception as e:
        logger.debug(f"PDF download error: {e}")
        return None, "error"

# ── Fee keywords ──────────────────────────────────────────────────────────────
FEE_PAGE_KEYWORDS = [
    "ค่าทิป", "ทิปไกด์", "ทิปหัวหน้า", "ทิปคนขับ", "tip",
    "มัดจำ", "เงินมัดจำ", "deposit",
    "พักเดี่ยว", "single supplement",
    "ค่าวีซ่า", "วีซ่า", "visa",
    "ทารก", "infant", "เด็กไม่มีเตียง",
    "ไม่รวม", "ราคาไม่รวม", "เงื่อนไข", "อัตราค่า",
]

# ── Text-based extraction ─────────────────────────────────────────────────────
def _parse_amount(s):
    try:
        return int(s.replace(",", ""))
    except Exception:
        return None

def extract_fees_from_text(text):
    fees = {}

    patterns_tip = [
        r"(?:ค่าทิป|ทิปไกด์|ทิปหัวหน้าทัวร์|ทิปคนขับรถ|ทิปไกด์และคนขับ)\s*[:\s]*(\d[\d,]+)\s*บาท",
        r"tip\s*(?:guide|driver)?\s*[:\s]*(\d[\d,]+)\s*(?:thb|baht|บาท)",
    ]
    for pat in patterns_tip:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            v = _parse_amount(m.group(1))
            if v and 100 <= v <= 15000:
                fees["tip_fee"] = v
                break

    patterns_dep = [
        r"(?:มัดจำ|เงินมัดจำ|ชำระมัดจำ)\s*[:\s]*(\d[\d,]+)\s*บาท",
        r"deposit\s*[:\s]*(\d[\d,]+)\s*(?:thb|baht|บาท)",
    ]
    for pat in patterns_dep:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            v = _parse_amount(m.group(1))
            if v and 500 <= v <= 50000:
                fees["deposit"] = v
                break

    patterns_single = [
        r"(?:พักเดี่ยว|ค่าพักเดี่ยว)\s*[:\s]*(\d[\d,]+)\s*บาท",
        r"single\s*supplement\s*[:\s]*(\d[\d,]+)\s*(?:thb|baht|บาท)",
    ]
    for pat in patterns_single:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            v = _parse_amount(m.group(1))
            if v and 500 <= v <= 30000:
                fees["single_supplement"] = v
                break

    patterns_visa = [
        r"(?:ค่าวีซ่า|วีซ่า)\s*[:\s]*(\d[\d,]+)\s*บาท",
        r"visa\s*fee\s*[:\s]*(\d[\d,]+)\s*(?:thb|baht|บาท)",
    ]
    for pat in patterns_visa:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            v = _parse_amount(m.group(1))
            if v and 500 <= v <= 20000:
                fees["visa_fee"] = v
                break

    if not fees.get("visa_fee"):
        if re.search(r"(?:ค่าวีซ่า|วีซ่า).*?รวมในราคา|รวม.*?ค่าวีซ่า", text):
            fees["visa_status"] = "รวมในราคา"
        elif re.search(r"(?:ค่าวีซ่า|วีซ่า).*?ไม่รวม|ไม่รวม.*?(?:ค่าวีซ่า|วีซ่า)", text):
            fees["visa_status"] = "ไม่รวมในราคา"
        elif re.search(r"ไม่ต้องใช้วีซ่า|ฟรีวีซ่า|visa\s*free", text, re.IGNORECASE):
            fees["visa_status"] = "ไม่ต้องใช้วีซ่า"

    m = re.search(r"(?:ทารก|infant)\s*[:\s]*(\d[\d,]+)\s*บาท", text, re.IGNORECASE)
    if m:
        v = _parse_amount(m.group(1))
        if v:
            fees["infant_fee"] = v

    m = re.search(r"เด็กไม่มีเตียง\s*[:\s]*(\d[\d,]+)\s*บาท", text)
    if m:
        v = _parse_amount(m.group(1))
        if v:
            fees["child_no_bed_fee"] = v

    return fees

def _confidence_from_count(n):
    if n >= 3:
        return "high"
    if n >= 1:
        return "medium"
    return "low"

# ── Vision OCR ────────────────────────────────────────────────────────────────
def extract_fees_with_vision(doc, page_nums):
    if not page_nums or not claude:
        return {}
    try:
        content = []
        for i in page_nums[:4]:
            page = doc[i]
            mat  = fitz.Matrix(1.5, 1.5)
            pix  = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
            b64  = base64.b64encode(pix.tobytes("png")).decode()
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64},
            })

        content.append({
            "type": "text",
            "text": (
                "สกัดข้อมูลค่าธรรมเนียมจาก PDF โปรแกรมทัวร์ไทยนี้\n"
                "ตอบเป็น JSON เท่านั้น:\n"
                "{\n"
                "  \"tip_fee\": <จำนวนบาท/ท่าน หรือ null>,\n"
                "  \"deposit\": <จำนวนบาทมัดจำ หรือ null>,\n"
                "  \"single_supplement\": <ค่าพักเดี่ยวบาท หรือ null>,\n"
                "  \"visa_fee\": <ค่าวีซ่าบาท หรือ null>,\n"
                "  \"visa_status\": <\"รวมในราคา\"|\"ไม่รวมในราคา\"|\"ไม่ต้องใช้วีซ่า\"|null>,\n"
                "  \"infant_fee\": <ค่าทารกบาท หรือ null>,\n"
                "  \"child_no_bed_fee\": <เด็กไม่มีเตียงบาท หรือ null>,\n"
                "  \"fee_raw_snippet\": <ข้อความค่าธรรมเนียมดิบ ไม่เกิน 400 ตัวอักษร หรือ null>,\n"
                "  \"confidence\": <\"high\"|\"medium\"|\"low\">\n"
                "}\n"
                "ถ้าไม่พบค่าใด ให้ใส่ null"
            ),
        })

        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": content}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return {}
        parsed = json.loads(m.group())
        expected = ["tip_fee", "deposit", "single_supplement", "visa_fee", "visa_status",
                    "infant_fee", "child_no_bed_fee", "fee_raw_snippet", "confidence"]
        return {k: v for k, v in parsed.items() if k in expected and v is not None}
    except json.JSONDecodeError as e:
        logger.warning(f"Vision JSON parse error: {e}")
        return {}
    except Exception as e:
        logger.error(f"Vision OCR error: {e}")
        return {}

# ── Per-tour processor ────────────────────────────────────────────────────────
def process_tour(tour):
    tour_id  = str(tour.get("id", ""))
    web_code = tour.get("web_code") or ""
    label    = web_code or tour_id
    # PDF filename uses numeric part of web_code (ap182432 -> tour_182432.pdf)
    # NOT the Supabase integer PK
    _wc_match = re.match(r'^ap(\d+)$', web_code)
    pdf_tour_id = _wc_match.group(1) if _wc_match else tour_id
    pdf_url  = PDF_BASE_URL.format(pdf_tour_id)
    now_iso  = datetime.now(timezone.utc).isoformat()
    base     = {"pdf_url": pdf_url, "fee_checked_at": now_iso}

    pdf_bytes, dl_status = download_pdf(pdf_url)
    logger.info(f"  [{label}] PDF download -> {dl_status}")
    if pdf_bytes is None:
        return {**base, "fee_extraction_status": "not_found", "fee_confidence": "low"}

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        logger.error(f"  [{label}] fitz.open error: {e}")
        return {**base, "fee_extraction_status": "error", "fee_confidence": "low"}

    try:
        fee_pages   = []
        image_pages = []
        page_texts  = {}

        for i in range(doc.page_count):
            t = doc[i].get_text().strip()
            page_texts[i] = t
            if any(kw in t for kw in FEE_PAGE_KEYWORDS):
                fee_pages.append(i)
            if len(t) < MIN_TEXT_CHARS:
                image_pages.append(i)

        if not fee_pages:
            fee_pages = list(range(max(0, doc.page_count - 3), doc.page_count))

        combined_text   = ""
        fee_source_page = None
        text_img_pages  = []

        for i in fee_pages[:6]:
            t = page_texts.get(i, "")
            if len(t) >= MIN_TEXT_CHARS:
                if fee_source_page is None:
                    fee_source_page = i + 1
                combined_text += f"\n{t}"
            else:
                text_img_pages.append(i)

        text_fees   = extract_fees_from_text(combined_text) if combined_text else {}
        vision_fees = {}

        need_vision = text_img_pages or (not text_fees and image_pages)
        if need_vision:
            vision_pages = text_img_pages if text_img_pages else fee_pages[:3]
            logger.info(f"  [{label}] Vision OCR pages: {[p+1 for p in vision_pages[:4]]}")
            vision_fees = extract_fees_with_vision(doc, vision_pages)

        fees = dict(text_fees)
        for k, v in vision_fees.items():
            if k == "confidence":
                continue
            if k not in fees and v is not None:
                fees[k] = v
        if not fee_source_page and vision_fees:
            fee_source_page = (text_img_pages[0] + 1) if text_img_pages else fee_pages[0] + 1

        key_fields  = ["tip_fee", "deposit", "single_supplement", "visa_fee"]
        found_count = sum(1 for f in key_fields if fees.get(f))
        visa_known  = bool(fees.get("visa_status"))

        if found_count >= 2 or (found_count == 1 and visa_known):
            ext_status = "found"
        elif found_count == 1 or visa_known:
            ext_status = "partial"
        else:
            ext_status = "not_found"

        confidence = vision_fees.get("confidence") or _confidence_from_count(found_count)

        summary_parts = []
        if fees.get("tip_fee"):
            summary_parts.append(f"ค่าทิปไกด์ {fees['tip_fee']:,} บ./ท่าน")
        if fees.get("deposit"):
            summary_parts.append(f"มัดจำ {fees['deposit']:,} บ.")
        if fees.get("single_supplement"):
            summary_parts.append(f"พักเดี่ยวเพิ่ม {fees['single_supplement']:,} บ.")
        if fees.get("visa_fee"):
            summary_parts.append(f"ค่าวีซ่า {fees['visa_fee']:,} บ.")
        elif fees.get("visa_status"):
            summary_parts.append(f"วีซ่า{fees['visa_status']}")
        if fees.get("infant_fee"):
            summary_parts.append(f"ทารก {fees['infant_fee']:,} บ.")

        result = {**base, "fee_extraction_status": ext_status, "fee_confidence": confidence}
        if fee_source_page:
            result["fee_source_page"] = fee_source_page
        if summary_parts:
            result["mandatory_fees_summary"] = " | ".join(summary_parts)

        fee_raw = fees.get("fee_raw_snippet") or combined_text[:400].strip()
        if fee_raw:
            result["fee_raw_snippet"] = fee_raw[:400]

        for field in ["tip_fee", "deposit", "single_supplement", "visa_fee",
                      "visa_status", "infant_fee", "child_no_bed_fee"]:
            if fees.get(field) is not None:
                result[field] = fees[field]

        logger.info(
            f"  [{label}] -> status={ext_status} conf={confidence} "
            f"found={found_count} keys={[k for k in key_fields if fees.get(k)]}"
        )
        return result

    finally:
        doc.close()

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("SUPABASE_URL and SUPABASE_KEY env vars required")
    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set -- Vision OCR disabled (image PDFs skipped)")

    logger.info(f"=== Fee Extractor start | batch={BATCH_SIZE} force={FORCE_RECHECK} ===")

    # ── Query: try filtered first, fallback to all if column missing ──────────
    base_params = {
        "select":    "id,web_code,tour_code_real",
        "is_active": "eq.true",
        "limit":     str(BATCH_SIZE),
        "order":     "id.asc",
    }

    tours = None
    if not FORCE_RECHECK:
        try:
            filtered = {**base_params, "fee_extraction_status": "is.null"}
            tours = sb_get("tours", filtered)
            logger.info(f"Filtered query OK -- {len(tours)} unprocessed tours")
        except Exception as e:
            logger.warning(f"Filtered query failed ({e}) -- column may not exist yet, fetching all")

    if tours is None:
        tours = sb_get("tours", base_params)
        logger.info(f"Unfiltered query -- {len(tours)} active tours to process")

    if not tours:
        logger.info("No tours to process, exiting")
        return

    # ── Log run start (best-effort) ───────────────────────────────────────────
    run_id = None
    try:
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/fee_extraction_runs",
            headers={**_sb_headers(), "Prefer": "return=representation"},
            json={"total_processed": 0, "force_recheck": FORCE_RECHECK, "status": "running"},
            timeout=10,
        )
        run_id = resp.json()[0]["id"] if resp.status_code in (200, 201) else None
    except Exception:
        pass

    # ── Process ───────────────────────────────────────────────────────────────
    counts = {"found": 0, "partial": 0, "not_found": 0, "error": 0}

    for i, tour in enumerate(tours, 1):
        tour_id  = tour.get("id")
        web_code = tour.get("web_code") or str(tour_id)
        logger.info(f"[{i}/{len(tours)}] Processing {web_code}")

        try:
            result = process_tour(tour)
            # Try full patch; if column missing fallback to safe subset
            try:
                sb_patch("tours", {"id": f"eq.{tour_id}"}, result)
            except Exception as patch_err:
                if "400" in str(patch_err):
                    safe = {k: v for k, v in result.items()
                            if k in ("tip_fee", "visa_fee", "visa_status", "single_supplement")}
                    if safe:
                        sb_patch("tours", {"id": f"eq.{tour_id}"}, safe)
                        logger.warning(f"  [{web_code}] Partial patch only: {list(safe.keys())}")
                else:
                    raise
            status = result.get("fee_extraction_status", "error")
            counts[status] = counts.get(status, 0) + 1
        except Exception as e:
            logger.error(f"  [{web_code}] FAILED: {e}")
            counts["error"] = counts.get("error", 0) + 1

        if i % 5 == 0:
            time.sleep(REQUEST_DELAY * 5)
        else:
            time.sleep(REQUEST_DELAY)

    # ── Summary ───────────────────────────────────────────────────────────────
    total = len(tours)
    logger.info(
        f"=== Done: total={total} | "
        f"found={counts['found']} partial={counts['partial']} "
        f"not_found={counts.get('not_found', 0)} error={counts.get('error', 0)} ==="
    )

    if run_id:
        try:
            sb_patch("fee_extraction_runs", {"id": f"eq.{run_id}"}, {
                "finished_at":     datetime.now(timezone.utc).isoformat(),
                "total_processed": total,
                "total_found":     counts["found"],
                "total_partial":   counts["partial"],
                "total_not_found": counts.get("not_found", 0),
                "total_error":     counts.get("error", 0),
                "status":          "done",
            })
        except Exception:
            pass


if __name__ == "__main__":
    main()
