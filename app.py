"""
AI Tour Concierge v2 — รวมทัวร์ไฟไหม้
แอดมิน AI ค้นหาและแนะนำโปรแกรมทัวร์จากเว็บจริง พร้อม Conversation Memory
"""

import os
import re
import json
import pickle
import logging
import threading
from datetime import date, datetime, timedelta
from flask import Flask, request, jsonify, abort
import anthropic
import requests
import pdfplumber
import fitz  # PyMuPDF
import base64
import io
from bs4 import BeautifulSoup

# ─── Config ──────────────────────────────────────────────────────────────────
app = Flask(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

VERIFY_TOKEN   = os.environ.get("VERIFY_TOKEN", "tourfiremai2024")
FB_PAGE_TOKEN  = os.environ.get("FB_PAGE_TOKEN", "")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT", "")

claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ─── Country Map ─────────────────────────────────────────────────────────────
COUNTRY_MAP = {
    # เอเชียตะวันออก
    "1": "เกาหลี", "2": "ญี่ปุ่น", "3": "ฮ่องกง",
    "4": "สิงคโปร์", "5": "จีน", "6": "มาเลเซีย",
    "7": "เวียดนาม", "8": "พม่า", "9": "ลาว",
    "18": "อินโดนีเซีย", "19": "ไต้หวัน", "29": "มาเก๊า",
    "104": "ฟิลิปปินส์",
    # เอเชียใต้ / กลาง
    "14": "อินเดีย", "20": "ภูฏาน", "182": "ศรีลังกา",
    "184": "ทิเบต", "173": "อุซเบกิสถาน", "256": "คาซัคสถาน",
    "164": "ปากีสถาน", "165": "มองโกเลีย",
    # ตะวันออกกลาง / แอฟริกา
    "72": "สหรัฐอาหรับฯ", "70": "จอร์แดน", "16": "อียิปต์",
    "167": "เคนย่า", "68": "แอฟริกาใต้", "161": "โมร็อกโก",
    "183": "อิหร่าน",
    # ยุโรปตะวันตก
    "42": "อังกฤษ", "64": "สวิตเซอร์แลนด์", "100": "เยอรมนี",
    "101": "ฝรั่งเศส", "102": "อิตาลี", "105": "สเปน",
    "159": "ออสเตรีย", "169": "กรีซ", "200": "โปรตุเกส",
    "213": "เบลเยี่ยม", "308": "เนเธอร์แลนด์", "2217": "เบเนลักซ์",
    # ยุโรปเหนือ / สแกนดิเนเวีย
    "47": "สแกนดิเนเวีย", "65": "ฟินแลนด์", "153": "สวีเดน",
    "162": "นอร์เวย์", "232": "เดนมาร์ก", "25": "ไอซ์แลนด์",
    "194": "ไอร์แลนด์", "197": "สกอตแลนด์",
    # ยุโรปตะวันออก / บอลข่าน
    "80": "ยุโรปตะวันออก", "66": "โครเอเชีย", "166": "โปแลนด์",
    "168": "จอร์เจีย", "71": "ตุรเคีย", "2213": "บอลติก",
    "2220": "โรมาเนีย", "275": "มอลตา", "276": "มอนเตเนโกร",
    # โอเชียเนีย / อเมริกา / อื่นๆ
    "10": "ออสเตรเลีย", "11": "นิวซีแลนด์",
    "12": "อเมริกา", "73": "แคนาดา", "174": "บราซิล",
    "175": "อาร์เจนติน่า", "226": "โคลอมเบีย", "272": "เม็กซิโก",
    # รัสเซีย / CIS
    "17": "รัสเซีย",
}

# ─── Conversation Store ───────────────────────────────────────────────────────
# { psid: {"history": [...], "last_active": datetime} }
HISTORY_FILE    = "/tmp/tourfiremai_history.pkl"
conversation_store: dict = {}
HISTORY_LIMIT  = 14      # messages kept per user (7 turns)
SESSION_TIMEOUT = timedelta(hours=24)

def _load_history() -> dict:
    """โหลด history จาก disk เมื่อ server start"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass
    return {}

def _save_history():
    """บันทึก history ลง disk — รองรับ server restart"""
    try:
        with open(HISTORY_FILE, "wb") as f:
            pickle.dump(conversation_store, f)
    except Exception as e:
        logger.warning(f"History save failed: {e}")

# โหลด history ที่มีอยู่เมื่อ server start
conversation_store.update(_load_history())
logger.info(f"Loaded {len(conversation_store)} active conversations from disk")

def get_history(psid: str) -> list:
    """คืน history ของ user (reset ถ้าหมด session)"""
    now = datetime.now()
    if psid in conversation_store:
        conv = conversation_store[psid]
        if now - conv["last_active"] > SESSION_TIMEOUT:
            conversation_store[psid] = {"history": [], "last_active": now}
    else:
        conversation_store[psid] = {"history": [], "last_active": now}
    conversation_store[psid]["last_active"] = now
    return conversation_store[psid]["history"]

def save_to_history(psid: str, role: str, content: str):
    """บันทึก message เข้า history ตัด trailing ถ้าเกิน limit"""
    history = get_history(psid)
    history.append({"role": role, "content": content})
    if len(history) > HISTORY_LIMIT:
        conversation_store[psid]["history"] = history[-HISTORY_LIMIT:]
    _save_history()

# ─── Facebook helpers ─────────────────────────────────────────────────────────
def send_message(recipient_id: str, text: str):
    """ส่งข้อความกลับไปที่ Messenger"""
    # Split long messages at newlines if > 2000 chars
    chunks = []
    while len(text) > 1950:
        split_at = text.rfind("\n", 0, 1950)
        if split_at == -1:
            split_at = 1950
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    chunks.append(text)

    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={FB_PAGE_TOKEN}"
    for chunk in chunks:
        if not chunk.strip():
            continue
        payload = {
            "recipient": {"id": recipient_id},
            "messaging_type": "RESPONSE",
            "message": {"text": chunk},
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.ok:
                logger.info(f"✅ Sent chunk ({len(chunk)} chars) to {recipient_id}")
            else:
                logger.error(f"❌ FB send error {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"❌ FB send exception: {e}")

def notify_telegram(message: str):
    """แจ้ง admin ทาง Telegram"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        logger.info("Telegram not configured, skip notify")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT, "text": message}, timeout=10)
        logger.info("📨 Telegram notified")
    except Exception as e:
        logger.error(f"Telegram error: {e}")

# ─── Tour data fetcher ────────────────────────────────────────────────────────
def fetch_tours(country_id: str) -> str:
    """ดึงข้อมูลทัวร์จาก tourfiremai.com พร้อม URL ของแต่ละโปรแกรม"""
    listing_url = f"https://www.tourfiremai.com/intertour/{country_id}/"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TourFiremaiBot/1.0)"}
    resp = requests.get(listing_url, headers=headers, timeout=20, allow_redirects=True)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    base_url = "https://www.tourfiremai.com"

    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "").strip()
        if not href or href.startswith("#") or href.startswith("javascript"):
            a_tag.replace_with(a_tag.get_text(strip=True))
            continue
        full_url = (base_url + href) if href.startswith("/") else href
        if re.match(r"https://www\.tourfiremai\.com/tour/ap\w+$", full_url):
            link_text = a_tag.get_text(strip=True)
            a_tag.replace_with(f"{link_text} [LINK:{full_url}]" if link_text else f"[LINK:{full_url}]")
        else:
            a_tag.replace_with(a_tag.get_text(strip=True))

    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    text = soup.get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:8000]

# PDF cache (in-memory): {program_url: extracted_text}
_PDF_CACHE: dict = {}

def extract_program_url_from_history(history: list) -> str | None:
    """ค้นหา URL โปรแกรมทัวร์ล่าสุดจาก conversation history"""
    pattern = re.compile(r'https://www\.tourfiremai\.com/tour/ap\w+')
    for msg in reversed(history):
        m = pattern.search(msg.get("content", ""))
        if m:
            return m.group(0)
    return None

def fetch_pdf_info(program_url: str) -> str:
    """ดาวน์โหลด PDF และสกัดข้อมูลสำคัญ
    - ลองอ่าน text ก่อน (fast)
    - ถ้าหน้าสำคัญเป็นรูปภาพ → ส่ง Claude Haiku Vision อ่านแทน (accurate)
    """
    if program_url in _PDF_CACHE:
        return _PDF_CACHE[program_url]

    m = re.search(r'/tour/ap(\d+)', program_url)
    if not m:
        return ""
    tour_id = m.group(1)
    pdf_url = f"https://www.tourfiremai.com/programtour/tour_{tour_id}.pdf"

    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; TourFiremaiBot/1.0)"}
        resp = requests.get(pdf_url, headers=headers, timeout=20)
        resp.raise_for_status()
        pdf_bytes = resp.content

        # ── ขั้น 1: หาหน้าสำคัญด้วย PyMuPDF ──────────────────────────────
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        keywords = ["มัดจำ", "ทิป", "วีซ่า", "เงื่อนไข", "ราคา", "อัตราค่า", "tip", "deposit", "visa", "ไม่รวม"]

        important_page_nums = []
        page_texts = {}
        for i in range(doc.page_count):
            t = doc[i].get_text().strip()
            page_texts[i] = t
            if any(kw in t for kw in keywords):
                important_page_nums.append(i)

        if not important_page_nums:
            # fallback: หน้าท้ายๆ
            important_page_nums = list(range(max(0, doc.page_count - 5), doc.page_count))

        # ── ขั้น 2: text extraction ──────────────────────────────────────
        text_result = ""
        image_needed_pages = []  # หน้าที่ text น้อยเกินไป → ต้องใช้ Vision

        for i in important_page_nums[:6]:
            t = page_texts.get(i, "")
            if len(t) > 80:
                text_result += f"\n[หน้า {i+1}]\n{t[:1500]}"
            else:
                image_needed_pages.append(i)  # น้อยกว่า 80 chars = น่าจะเป็นรูป

        # ── ขั้น 3: Vision fallback สำหรับหน้าที่เป็นรูปภาพ ──────────────
        if image_needed_pages:
            logger.info(f"PDF vision fallback for pages: {[p+1 for p in image_needed_pages]}")
            vision_result = _read_pdf_pages_with_vision(doc, image_needed_pages[:4], tour_id)
            if vision_result:
                text_result += f"\n[Vision OCR]\n{vision_result}"

        doc.close()

        result = f"[ข้อมูลจาก PDF โปรแกรม {tour_id}]\n{text_result.strip()}"
        result = result[:5000]
        _PDF_CACHE[program_url] = result
        logger.info(f"PDF fetched: {pdf_url} ({len(result)} chars, vision_pages={image_needed_pages})")
        return result

    except Exception as e:
        logger.error(f"fetch_pdf_info error: {e}")
        return ""


def _read_pdf_pages_with_vision(doc: fitz.Document, page_nums: list, tour_id: str) -> str:
    """แปลง PDF pages เป็นรูปแล้วส่ง Claude Haiku Vision อ่าน"""
    try:
        content = []
        for i in page_nums:
            page = doc[i]
            mat = fitz.Matrix(1.5, 1.5)  # 1.5x zoom — พอสำหรับอ่าน text
            pix = page.get_pixmap(matrix=mat)
            b64 = base64.b64encode(pix.tobytes("png")).decode()
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64}
            })
            logger.info(f"  Rendered page {i+1}: {len(b64)//1024} KB")

        content.append({
            "type": "text",
            "text": (
                "นี่คือหน้า PDF โปรแกรมทัวร์ สกัดข้อมูลต่อไปนี้จากภาพ:\n"
                "1. มัดจำ/เงินมัดจำ — จำนวนเงิน\n"
                "2. วีซ่า — ค่าวีซ่า หรือ รวม/ไม่รวมในราคาทัวร์\n"
                "3. ทิปไกด์/ทิปคนขับ — จำนวนเงิน\n"
                "4. เงื่อนไขการยกเลิก — สรุปสั้นๆ\n"
                "5. ราคาทัวร์ (ถ้ามีในภาพ)\n"
                "ถ้าไม่พบข้อมูลใดในภาพ ให้ตอบว่า 'ไม่พบในเอกสาร'\n"
                "ตอบเป็นภาษาไทย กระชับ"
            )
        })

        vision_resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": content}]
        )
        return vision_resp.content[0].text

    except Exception as e:
        logger.error(f"_read_pdf_pages_with_vision error: {e}")
        return ""


# ─── AI — System Prompt ───────────────────────────────────────────────────────
def _system_prompt() -> str:
    today = date.today().strftime("%-d %B %Y")
    return f"""คุณคือ "แอดมิน AI" ของเพจ รวมทัวร์ไฟไหม้
บริษัท อัพ-โอเปอเรชั่น จำกัด | เว็บ: www.tourfiremai.com | LINE: @tourfiremai
วันนี้: {today}

══════════════════════════════════
บุคลิกและสไตล์การตอบ
══════════════════════════════════
- ฉลาด ชัดเจน ขายเป็น ไม่แถ — ตอบตรงประเด็น ไม่เขียนเรียงความ
- ใช้ภาษาไทยเป็นกันเองแต่มืออาชีพ ลงท้าย ค่ะ/นะคะ
- ใช้ emoji ได้ไม่เกิน 2 ตัวต่อข้อความ
- ตอบสั้นกระชับ ถ้าข้อมูลยาวให้แบ่งเป็นบรรทัด อย่าบล็อกข้อความยาวเป็นพรรณๆ
- ถามทีละ 1 คำถามเท่านั้น เลือกสิ่งที่สำคัญที่สุดก่อน ห้ามถามหลายอย่างพร้อมกัน
- ถ้าลูกค้าบอกงบหรือปลายทางแล้ว → เสนอแนวทางหรือโปรแกรมก่อน แล้วค่อยถามต่อ อย่าถามก่อนโดยไม่เสนออะไร

══════════════════════════════════
กฎความจำบริบทและตัวเลือกที่เสนอ
══════════════════════════════════
จำข้อมูลสำคัญจากบทสนทนาปัจจุบันเสมอ:
- ประเทศ/เมือง/แนวทริป
- เดือนหรือวันที่เดินทาง
- จำนวนคน และงบประมาณ
- โปรแกรมที่เคยเสนอ — ตัวเลือก 1-3 ล่าสุดที่ AI เสนอ

ถ้าลูกค้าพิมพ์ว่า "ตัวที่ 1" / "ตัวที่ 2" / "ตัวที่ 3" / "อันนี้" / "ตัวนี้" / "เช็กเลย" / "สนใจอันนี้"
→ ให้ถือว่าหมายถึงโปรแกรมล่าสุดที่เพิ่งคุยกัน ห้ามถามซ้ำว่าอยากไปประเทศไหน

ถ้าลูกค้าพิมพ์ "เช็กเลย" หลังเลือกโปรแกรม → ถือเป็น hot lead
ให้สรุปข้อมูลโปรแกรมนั้นทันที แล้วถามเฉพาะ ชื่อผู้ติดต่อ + เบอร์/LINE เท่านั้น:

"ได้เลยค่ะ แอดมิน AI สรุปให้ทีมงานเช็กตัวนี้นะคะ 🔍

โปรแกรม: [ชื่อโปรแกรม]
รหัส: [รหัส]
วันเดินทาง: [วัน]
จำนวน: [จำนวนคน]
ราคาเริ่มต้น: [ราคา]

ทีมงานจะเช็กที่นั่งและราคาอัปเดตให้อีกครั้งค่ะ
ขอชื่อผู้ติดต่อและเบอร์โทร/LINE ไว้ให้ทีมงานติดต่อกลับได้ไหมคะ?"

══════════════════════════════════
ผลิตภัณฑ์หลัก — ทัวร์ไฟไหม้
══════════════════════════════════
ทัวร์ไฟไหม้ = ทัวร์ใกล้วันเดินทาง (ภายใน 30-45 วัน) ที่ Wholesale ปรับลดราคาพิเศษ
เนื่องจากต้องการเติมที่นั่งที่เหลืออยู่ ราคามักถูกกว่าปกติ 20-50%
- ถ้าลูกค้าพร้อมเดินทางเร็ว หรือยังไม่ได้วางแผน → แนะนำทัวร์ไฟไหม้ก่อน
- ถ้ามีโปรแกรมใกล้เดินทาง (≤45 วัน) ในข้อมูลทัวร์ → ระบุว่าเป็น "ทัวร์ไฟไหม้ 🔥 โอกาสดี!"

══════════════════════════════════
กฎ PDF Detail
══════════════════════════════════
ถ้าลูกค้าถามเรื่องต่อไปนี้ และมีโปรแกรมที่เลือกไว้แล้ว:
- มัดจำ / เงินมัดจำ
- วีซ่า (รวม/ไม่รวม)
- ทิปไกด์ / ทิปคนขับ
- พักเดี่ยว (single supplement)
- เด็ก / ทารก
- เงื่อนไขการยกเลิก
- รวมอะไร / ไม่รวมอะไร
- โรงแรม / สายการบิน / itinerary รายละเอียด

→ ต้อง trigger action=detail_pdf ก่อนตอบ ห้ามตอบว่า "ดูได้ใน PDF" เฉยๆ

ถ้าอ่าน PDF แล้วพบข้อมูล → ตอบจาก PDF โดยตรง กระชับ ระบุว่า "ตามไฟล์โปรแกรมนี้"
ถ้าอ่าน PDF แล้วไม่พบ → ตอบว่า "ในไฟล์โปรแกรมนี้ยังไม่พบข้อมูลส่วนนี้ชัดเจนค่ะ แอดมิน AI ขอส่งให้ทีมงานเช็กจากเอกสารอัปเดตให้อีกครั้งนะคะ"

══════════════════════════════════
เมื่อลูกค้าขอดูโปรแกรมบนเว็บ
══════════════════════════════════
ถ้าลูกค้าถามว่า: "ดูโปรแกรมบนเว็บได้ไหม" / "หาในเว็บให้หน่อย" / "มีโปรอะไรบ้าง"
/ "มีทัวร์ไฟไหม้ไหม" / "ช่วยแนะนำจากเว็บ"

→ ห้ามตอบแค่ส่งลิงก์เว็บให้ลูกค้าไปหาเอง
→ ให้ trigger action=search หรือ flash_sale แล้วคัด 1-3 โปรแกรมจากเว็บมาตอบ
→ ถ้าข้อมูลยังไม่พอ ให้ถามเพียง 1 คำถามที่สำคัญที่สุด เช่น "สนใจเดินทางเดือนไหนคะ?"

══════════════════════════════════
ขั้นตอนเมื่อลูกค้าสนใจจอง
══════════════════════════════════
ขั้นที่ 1 — สรุปโปรแกรม + เก็บข้อมูลติดต่อก่อน (อย่าถามทุกอย่างพร้อมกัน):
   "ขอชื่อผู้ติดต่อและเบอร์โทร/LINE ไว้ให้ทีมงานติดต่อกลับได้ไหมคะ?"

ขั้นที่ 2 — ทีมงานจะเก็บข้อมูลที่เหลือเองเมื่อติดต่อกลับ:
   (ชื่อผู้เดินทางทุกท่าน, วันเดินทางจริง, จำนวนคน, พาสปอร์ต ฯลฯ)

เมื่อได้ชื่อ+เบอร์แล้ว → แจ้งว่า:
"ส่งข้อมูลให้ทีมงานแล้วค่ะ จะติดต่อกลับเพื่อยืนยันที่นั่งและแจ้งรายละเอียดการชำระเงินค่ะ"
หมายเหตุ: AI ไม่สามารถยืนยันที่นั่ง รับเงิน หรือยืนยันราคา final ได้ — ทีมงานจะดำเนินการ

══════════════════════════════════
เมื่อลูกค้าขอคุยกับคนจริง / เซลล์ / แอดมิน
══════════════════════════════════
ตอบทันที: "รับทราบค่ะ ส่งให้ทีมงานแล้ว จะติดต่อกลับเร็วๆ นี้ หรือทัก LINE @tourfiremai ได้เลยค่ะ 😊"
ห้ามถามข้อมูลเพิ่มหรือพยายามโน้มน้าวต่อ — จบบทสนทนาทันที

══════════════════════════════════
การแสดงข้อมูลทัวร์
══════════════════════════════════
เมื่อมีข้อมูลทัวร์จากเว็บ:
- เลือก 1-3 โปรแกรมที่เหมาะที่สุด อธิบายว่าทำไมจึงเหมาะ 1 ประโยคต่อตัวเลือก
- แสดง: รหัสโปรแกรม | ชื่อทัวร์ | จำนวนวัน | ราคาเริ่มต้น | สายการบิน | วันเดินทาง | ลิงก์
- ถ้าใกล้เดินทาง (≤45 วัน) → ระบุว่าเป็น "ทัวร์ไฟไหม้ 🔥 โอกาสดี!"
- ถ้างบไม่พอ → แนะนำทางเลือกอื่นหรือประเทศใกล้เคียงได้เลย

เมื่อมีข้อมูลจาก PDF โปรแกรม:
- ตอบคำถามที่ลูกค้าถามโดยตรงจากข้อมูลใน PDF เท่านั้น

กฎวันเดินทาง:
- ≤ 3 รอบ → แสดงทุกรอบ
- > 3 รอบ span ≤ 30 วัน → แสดงวันแรก-วันสุดท้าย
- > 3 รอบ span > 30 วัน → แสดงเดือน
- ข้ามรอบที่ผ่านมาแล้ว

══════════════════════════════════
กฎห้ามรีเซ็ตบทสนทนา
══════════════════════════════════
ห้ามทักทายใหม่หรือถาม "อยากไปเที่ยวที่ไหนคะ?" ถ้าบทสนทนาก่อนหน้ามีข้อมูลอยู่แล้ว

ถ้าลูกค้าพิมพ์สั้นๆ เช่น "ไหนครับ", "ได้ยัง", "ส่งมา", "มีไหม", "รออยู่", "ดูให้หน่อย"
→ ให้ตีความว่าเขากำลังตามคำตอบจากเรื่องก่อนหน้า ต้องใช้บริบทเดิมมาตอบต่อทันที

ตัวอย่าง:
ลูกค้า: เอาโอซาก้าครับ งบ 40,000
AI: ขอค้นหาโอซาก้าให้นะคะ
ลูกค้า: ไหนครับ
✅ ถูก: "ขอโทษที่ให้รอนะคะ กำลังคัดโปรแกรมโอซาก้างบ 40,000 ให้ค่ะ" แล้ว trigger search ต่อ
❌ ห้าม: "สวัสดีค่ะ อยากไปเที่ยวที่ไหนคะ?"

══════════════════════════════════
เมื่อค้นหาแล้วไม่เจอโปรแกรมตรงเงื่อนไข
══════════════════════════════════
อย่าถามเริ่มใหม่ ให้เสนอทางเลือกทันที เช่น:
"ตอนนี้ยังไม่เจอโอซาก้าที่ตรงงบ 40,000 พอดีค่ะ แอดมิน AI แนะนำได้ 2 ทาง:
1. ดูญี่ปุ่นเมืองใกล้เคียง เช่น โตเกียว/นาโกย่า ที่งบใกล้เคียงกัน
2. ให้ทีมงานเช็กโปรโอซาก้าล่าสุดโดยตรง
ให้แอดมินส่งทีมงานเช็กให้เลยไหมคะ?"

══════════════════════════════════
สิ่งที่ห้ามทำเด็ดขาด
══════════════════════════════════
- ยืนยันที่นั่งว่าง หรือยืนยันราคา final
- บอกชื่อ Wholesale / บริษัทจัดทัวร์
- รับจอง รับเงิน หรือบอกว่าจองสำเร็จ
- เดาข้อมูลที่ไม่มีในข้อมูลทัวร์
- ระบุตัวเลขมัดจำ/วีซ่า/ทิปโดยไม่มีข้อมูลจาก PDF
- ส่งแค่ลิงก์เว็บโดยไม่คัดโปรแกรมมาให้
"""


# ─── AI — Call 1: Decide Action ───────────────────────────────────────────────
def decide_action(user_message: str, history: list) -> dict:
    """
    วิเคราะห์ว่าต้องทำอะไร คืน JSON:
    {
      "action": "search"|"detail"|"detail_pdf"|"flash_sale"|"handoff"|"reply"|"continue",
      "country_id": "2"|null,
      "selected_option_index": 1|2|3|null,
      "uses_previous_option": true|false,
      "lead_stage": "cold"|"warm"|"hot"|"booking"
    }
    """
    history_text = ""
    for msg in history[-10:]:
        role = "ลูกค้า" if msg["role"] == "user" else "AI"
        history_text += f"{role}: {msg['content'][:250]}\n"

    prompt = (
        f"บทสนทนาที่ผ่านมา:\n{history_text}\n"
        f"--- ข้อความล่าสุดของลูกค้า (สำคัญที่สุด): {user_message} ---\n\n"

        "ตอบเป็น JSON เท่านั้น (ห้ามมีข้อความอื่น):\n"
        "{\n"
        '  "action": "search" | "detail" | "detail_pdf" | "flash_sale" | "handoff" | "reply" | "continue",\n'
        '  "country_id": "เลขประเทศ หรือ null",\n'
        '  "selected_option_index": 1 | 2 | 3 | null,\n'
        '  "uses_previous_option": true | false,\n'
        '  "lead_stage": "cold" | "warm" | "hot" | "booking"\n'
        "}\n\n"

        "=== กฎ action (เรียงตามความสำคัญ) ===\n\n"

        "⚠️ กฎ CONTINUATION — ตรวจสอบก่อนทุกกฎอื่น:\n"
        "ถ้าข้อความล่าสุดเป็น คำสั้นๆ เช่น 'ไหน', 'ไหนครับ', 'ไหนคะ', 'ได้ยัง', 'รออยู่', 'ส่งมา', 'มีไหม', 'หาได้ไหม', 'ดูให้หน่อย', 'แล้วไง', 'ยังไง'\n"
        "AND ใน history ก่อนหน้า AI เคยบอกว่าจะค้นหา/เช็ก/ดึงข้อมูล/รอสักครู่\n"
        "→ action=continue, country_id=ประเทศล่าสุดจาก history, lead_stage ตาม context\n\n"

        "action=search: ลูกค้าต้องการดูโปรแกรมทัวร์ประเทศที่ระบุ รวมถึงการเปลี่ยนประเทศ\n"
        "action=detail: ลูกค้าขอดูรายละเอียดทัวร์โปรแกรมใดโปรแกรมหนึ่ง\n"
        "action=detail_pdf: ลูกค้าถามมัดจำ/วีซ่า/ทิป/พักเดี่ยว/เงื่อนไขยกเลิก/รายละเอียด itinerary/โรงแรม/สายการบิน/รวมอะไร — และมีโปรแกรมที่เลือกไว้ใน context\n"
        "action=flash_sale: ลูกค้าถามทัวร์ไฟไหม้/โปรโมชั่นพิเศษ/ดีลร้อน\n"
        "action=handoff: ลูกค้าพร้อมจอง/สนใจจอง/ขอคุยเซลล์/เช็กที่นั่ง/ขอราคา final/ขอส่วนลด/ยกเลิก\n"
        "action=reply: ทักทาย/ถามทั่วไป/ยังไม่ระบุประเทศ/ยุโรปรวม — ใช้เฉพาะตอนเริ่มบทสนทนาใหม่จริงๆ เท่านั้น\n\n"

        "=== กฎ selected_option_index และ uses_previous_option ===\n"
        "ถ้าลูกค้าพิมพ์ 'ตัวที่ 1/2/3', 'อันนี้', 'ตัวนี้', 'สนใจอันนี้' → uses_previous_option=true, selected_option_index=เลขที่ระบุ\n"
        "ถ้าลูกค้าพิมพ์ 'เช็กเลย' หลังมีโปรแกรมใน context → action=handoff, uses_previous_option=true, lead_stage=hot\n"
        "ถ้าลูกค้าเปลี่ยนประเทศจริง → uses_previous_option=false, country_id ใหม่\n\n"

        "=== กฎ lead_stage ===\n"
        "cold: เพิ่งทักมา ยังไม่รู้จะไปไหน\n"
        "warm: รู้ประเทศ/ช่วงเวลา เริ่มดูโปรแกรม\n"
        "hot: เลือกโปรแกรมแล้ว ถามรายละเอียด/มัดจำ/วีซ่า\n"
        "booking: บอกจอง/เช็กเลย/ขอติดต่อกลับ\n\n"

        "=== Country IDs ===\n"
        "เอเชีย: ญี่ปุ่น=2, เกาหลี=1, เวียดนาม=7, จีน=5, ฮ่องกง=3, สิงคโปร์=4, มาเลเซีย=6, ไต้หวัน=19, พม่า=8, ลาว=9, อินโดนีเซีย=18, มาเก๊า=29, ฟิลิปปินส์=104\n"
        "ยุโรป: อิตาลี=102, สวิตฯ=64, สแกนดิ=47, อังกฤษ=42, เยอรมนี=100, ตุรเคีย=71, ออสเตรีย=159, สเปน=105, ฝรั่งเศส=101, กรีซ=169, โปรตุเกส=200, ยุโรปตะวันออก=80\n"
        "อื่นๆ: ออสเตรเลีย=10, นิวซีแลนด์=11, อเมริกา=12, ดูไบ/UAE=72, อินเดีย=14, อียิปต์=16, รัสเซีย=17, จอร์เจีย=168, คาซัคสถาน=256\n\n"

        "ตัวอย่าง continuation:\n"
        "AI พูดก่อน: 'ขอค้นหาโอซาก้าให้นะคะ...' → ลูกค้า: 'ไหนครับ' → action=continue country_id=2\n"
        "AI พูดก่อน: 'ขอดึงข้อมูลสักครู่ค่ะ' → ลูกค้า: 'ได้ยัง' → action=continue country_id=country ล่าสุด\n\n"

        "ตัวอย่างอื่น:\n"
        "'เปลี่ยนเป็นเกาหลีแทน' → action=search country_id=1 uses_previous_option=false\n"
        "'สนใจตัวที่ 2' → uses_previous_option=true selected_option_index=2\n"
        "'มัดจำเท่าไหร่' (มีโปรแกรมใน context) → action=detail_pdf lead_stage=hot\n"
        "'เช็กเลย' → action=handoff uses_previous_option=true lead_stage=hot"
    )

    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        m = re.search(r"\{.*?\}", raw, re.DOTALL)
        if m:
            data = json.loads(m.group())
            data["action"] = data.get("action", "reply")
            cid = data.get("country_id")
            data["country_id"] = str(cid) if cid and str(cid) != "null" else None
            data["selected_option_index"] = data.get("selected_option_index")
            data["uses_previous_option"] = bool(data.get("uses_previous_option", False))
            data["lead_stage"] = data.get("lead_stage", "cold")
            return data
    except Exception as e:
        logger.error(f"decide_action error: {e}")

    return {"action": "reply", "country_id": None, "selected_option_index": None,
            "uses_previous_option": False, "lead_stage": "cold"}


# ─── AI — Call 2: Generate Response ──────────────────────────────────────────
def generate_response(user_message: str, history: list, tour_data: str = "", is_handoff: bool = False) -> str:
    """
    Claude สร้างคำตอบแบบ natural conversation
    history = messages ก่อนหน้า (ไม่รวม user_message ปัจจุบัน)
    """
    # Build message array from history
    messages = []
    for msg in history[-10:]:
        messages.append({"role": msg["role"], "content": msg["content"]})

    # Build user content
    if tour_data:
        user_content = (
            f"{user_message}\n\n"
            "--- ข้อมูลทัวร์จากเว็บ tourfiremai.com (ดึงตอนนี้) ---\n"
            f"{tour_data[:4000]}\n"
            "---\n"
            "ใช้ข้อมูลทัวร์ด้านบนในการตอบ คัดเลือก 1-3 โปรแกรมที่เหมาะที่สุดกับความต้องการลูกค้า "
            "พร้อมเหตุผล 1 ประโยคต่อตัวเลือก"
        )
    elif is_handoff:
        user_content = (
            f"{user_message}\n\n"
            "[คำแนะนำ: ลูกค้าต้องการข้อมูลที่ต้องให้ทีมงานเช็ก ให้บอกว่าจะส่งทีมงาน แต่ถ้ามีข้อมูลเบื้องต้นตอบได้ก็ตอบก่อน]"
        )
    else:
        user_content = (
            f"{user_message}\n\n"
            "[หมายเหตุสำหรับ AI: ไม่มีข้อมูลทัวร์ใหม่สำหรับข้อความนี้ "
            "ถ้าลูกค้าเปลี่ยนประเทศหรือเปลี่ยนความต้องการ อย่านำทัวร์จากการสนทนาก่อนหน้ามาแสดงซ้ำ "
            "ให้ถามสิ่งที่ต้องการเพิ่มเติมหรืออธิบายว่ากำลังจะหาข้อมูลให้]"
        )

    messages.append({"role": "user", "content": user_content})

    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            system=_system_prompt(),
            messages=messages
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.error(f"generate_response error: {e}")
        return "ขออภัยค่ะ ระบบมีปัญหาชั่วคราว กรุณาลองใหม่อีกครั้ง หรือติดต่อแอดมินได้เลยนะคะ 😊"


# ─── Core message processing ──────────────────────────────────────────────────
def process_message(sender_id: str, text: str):
    """Main logic — รันใน background thread"""
    logger.info(f"Processing [{sender_id}]: {text[:80]}")
    try:
        # ดึง history ก่อน save message ใหม่
        history = list(get_history(sender_id))  # snapshot

        # Call 1: decide what to do
        action_data = decide_action(text, history)
        action               = action_data.get("action", "reply")
        country_id           = action_data.get("country_id")
        selected_option_idx  = action_data.get("selected_option_index")
        uses_previous        = action_data.get("uses_previous_option", False)
        lead_stage           = action_data.get("lead_stage", "cold")
        logger.info(f"Action: {action}, country_id: {country_id}, lead_stage: {lead_stage}, "
                    f"selected_idx: {selected_option_idx}, uses_prev: {uses_previous}")

        tour_data   = ""
        is_handoff  = False

        # action=continue → ใช้ country_id ล่าสุดจาก history แล้ว search เหมือนเดิม
        if action == "continue":
            if not country_id:
                # หา country_id จาก history (มองหา country_id ที่เคยใช้ก่อนหน้า)
                for msg in reversed(history):
                    c = re.search(r'country_id["\s:]+(\d+)', msg.get("content", ""))
                    if c:
                        country_id = c.group(1)
                        break
            action = "search"  # treat เหมือน search ปกติ
            logger.info(f"Continuation detected → search country_id={country_id}")

        # Fetch tour data if needed
        if action in ("search", "detail") and country_id:
            country_name = COUNTRY_MAP.get(country_id, country_id)
            logger.info(f"Fetching tours: {country_name} (id={country_id})")
            try:
                tour_data = fetch_tours(country_id)
            except Exception as e:
                logger.error(f"fetch_tours error: {e}")
                tour_data = ""

        # Fetch PDF info for specific program questions (มัดจำ/วีซ่า/ทิป)
        if action == "detail_pdf":
            program_url = extract_program_url_from_history(history)
            if program_url:
                logger.info(f"Fetching PDF for: {program_url}")
                try:
                    tour_data = fetch_pdf_info(program_url)
                except Exception as e:
                    logger.error(f"fetch_pdf_info error: {e}")
                    tour_data = ""
            else:
                # ไม่มี URL ใน history → ตอบธรรมดา
                action = "reply"

        # Notify admin for flash_sale or handoff
        if action == "flash_sale":
            notify_telegram(
                f"🔥 ทัวร์ไฟไหม้!\nPSID: {sender_id}\nข้อความ: {text}"
            )
        elif action == "handoff":
            is_handoff = True
            stage_emoji = {"hot": "🔔", "booking": "📋", "warm": "💬"}.get(lead_stage, "📩")
            notify_telegram(
                f"{stage_emoji} Lead [{lead_stage.upper()}]\nPSID: {sender_id}\nข้อความ: {text}"
            )

        # Save user message to history
        save_to_history(sender_id, "user", text)

        # Call 2: generate natural response
        reply = generate_response(text, history, tour_data, is_handoff)

        # Save AI reply to history
        save_to_history(sender_id, "assistant", reply)

        send_message(sender_id, reply)

    except Exception as e:
        logger.error(f"process_message error: {e}", exc_info=True)
        try:
            send_message(
                sender_id,
                "ขออภัยค่ะ ระบบมีปัญหาชั่วคราว กรุณาลองใหม่ หรือทักแอดมินได้เลยนะคะ 🙏"
            )
        except Exception:
            pass


# ─── Webhook routes ───────────────────────────────────────────────────────────
@app.route("/webhook", methods=["GET"])
def verify():
    mode      = request.args.get("hub.mode")
    token     = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("Webhook verified ✅")
        return challenge, 200
    logger.warning(f"Webhook verify failed: mode={mode}, token={token}")
    abort(403)


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True)
    if not data or data.get("object") != "page":
        return jsonify({"status": "ignored"}), 200

    for entry in data.get("entry", []):
        for msg_event in entry.get("messaging", []):
            if msg_event.get("message", {}).get("is_echo"):
                continue
            sender_id = msg_event.get("sender", {}).get("id")
            text      = msg_event.get("message", {}).get("text", "").strip()
            if not sender_id or not text:
                continue
            t = threading.Thread(target=process_message, args=(sender_id, text))
            t.daemon = True
            t.start()

    return jsonify({"status": "ok"}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "TourFiremai AI Concierge v2"}), 200


# ─── Entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
