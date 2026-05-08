"""
AI Sales Bot — รวมทัวร์ไฟไหม้
Facebook Messenger Webhook Server
"""

import os
import re
import json
import logging
import threading
from datetime import date
from flask import Flask, request, jsonify, abort
import anthropic
import requests
from bs4 import BeautifulSoup

# ─── Config ────────────────────────────────────────────────────────────────────
app = Flask(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

VERIFY_TOKEN   = os.environ.get("VERIFY_TOKEN", "tourfiremai2024")
FB_PAGE_TOKEN  = os.environ.get("FB_PAGE_TOKEN", "")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")

claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

COUNTRY_MAP = {
    "1": "เกาหลี", "2": "ญี่ปุ่น", "3": "ฮ่องกง",
    "4": "สิงคโปร์", "5": "จีน", "6": "มาเลเซีย",
    "7": "เวียดนาม", "19": "ไต้หวัน",
    "42": "อังกฤษ", "47": "สแกนดิเนเวีย", "64": "สวิตเซอร์แลนด์",
    "71": "ตุรเคีย", "80": "ยุโรปตะวันออก", "100": "เยอรมนี",
    "101": "ฝรั่งเศส", "102": "อิตาลี", "105": "สเปน",
    "159": "ออสเตรีย", "169": "กรีซ", "200": "โปรตุเกส",
}

# ─── Facebook helpers ───────────────────────────────────────────────────────────
def send_message(recipient_id: str, text: str):
    """ส่งข้อความกลับไปที่ Messenger (จำกัด 2000 ตัวอักษร)"""
    text = text[:2000]
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={FB_PAGE_TOKEN}"
    payload = {
        "recipient": {"id": recipient_id},
        "messaging_type": "RESPONSE",
        "message": {"text": text},
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.ok:
            logger.info(f"✅ Sent reply to {recipient_id}")
        else:
            logger.error(f"❌ FB send error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"❌ FB send exception: {e}")

# ─── Tour data fetcher ──────────────────────────────────────────────────────────
def fetch_tours(country_id: str) -> str:
    """ดึงข้อมูลทัวร์จาก tourfiremai.com พร้อม URL ของแต่ละโปรแกรม"""
    listing_url = f"https://www.tourfiremai.com/intertour/{country_id}/"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TourFiremaiBot/1.0)"}
    resp = requests.get(listing_url, headers=headers, timeout=20, allow_redirects=True)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    base_url = "https://www.tourfiremai.com"

    # แทน <a> tags ที่เป็น tour detail links ด้วย text + URL
    # (เก็บเฉพาะ links ที่ยาวกว่า listing page = น่าจะเป็น individual tour page)
    for a_tag in soup.find_all('a', href=True):
        href = a_tag.get('href', '').strip()
        if not href or href.startswith('#') or href.startswith('javascript'):
            a_tag.replace_with(a_tag.get_text(strip=True))
            continue
        if href.startswith('/'):
            full_url = base_url + href
        elif href.startswith('http'):
            full_url = href
        else:
            a_tag.replace_with(a_tag.get_text(strip=True))
            continue
        # เก็บเฉพาะ individual tour detail links: /tour/apXXXXXX
        if re.match(r'https://www\.tourfiremai\.com/tour/ap\w+$', full_url):
            link_text = a_tag.get_text(strip=True)
            a_tag.replace_with(f"{link_text} [LINK:{full_url}]" if link_text else f"[LINK:{full_url}]")
        else:
            a_tag.replace_with(a_tag.get_text(strip=True))

    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    text = soup.get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:8000]

# ─── Claude helpers ─────────────────────────────────────────────────────────────
def analyze_intent(user_message: str) -> str:
    """Claude Round 1 — จำแนก Track A / B / B2 / C"""
    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        system=(
            "คุณคือระบบ AI วิเคราะห์ข้อความลูกค้า สำหรับเพจ 'รวมทัวร์ไฟไหม้'\n\n"
            "ตอบในรูปแบบที่กำหนดเท่านั้น ห้ามมีข้อความอื่น:\n\n"
            "Track A — ลูกค้าถามถึง 'ทัวร์ไฟไหม้' หรือโปรโมชั่นพิเศษ/ดีลร้อนแรง:\n"
            "  → [NOTIFY_ADMIN]\n\n"
            "Track B — ลูกค้าถามทัวร์ทั่วไปและระบุประเทศ/จุดหมาย (ต้องการดูหลายโปรแกรม):\n"
            "  → [SEARCH_TOURS] country_id=X\n"
            "  ID เอเชีย: ญี่ปุ่น=2, เกาหลี=1, เวียดนาม=7, จีน=5, ฮ่องกง=3, สิงคโปร์=4, มาเลเซีย=6, ไต้หวัน=19\n"
            "  ID ยุโรป: อิตาลี=102, สวิตเซอร์แลนด์=64, สแกนดิเนเวีย=47, อังกฤษ=42, เยอรมนี=100, ตุรเคีย=71, ออสเตรีย=159, สเปน=105, ฝรั่งเศส=101, กรีซ=169, โปรตุเกส=200, ยุโรปตะวันออก=80\n"
            "  หากลูกค้าพูดว่า ยุโรป โดยไม่ระบุประเทศ → [ASK_COUNTRY] (ถามประเทศเพิ่ม)\n\n"
            "Track B2 — ลูกค้าต้องการรายละเอียดเต็มของโปรแกรมเดียว:\n"
            "  (เช่น ระบุชื่อทัวร์, 'โปรแกรมที่ 1', 'ขอแค่ 1 ตัว', 'ขอดูรายละเอียด'):\n"
            "  → [TOUR_DETAIL] country_id=X\n\n"
            "Track C — ลูกค้าทักทาย/ถามทั่วไป/ยังไม่ระบุประเทศ:\n"
            "  → [ASK_COUNTRY]\n\n"
            "ตัวอย่าง:\n"
            "'มีทัวร์ไฟไหม้ไหม' → [NOTIFY_ADMIN]\n"
            "'อยากไปญี่ปุ่น' → [SEARCH_TOURS] country_id=2\n"
            "'ขอรายละเอียดทัวร์โตเกียว DOUBLE FREEDAY' → [TOUR_DETAIL] country_id=2\n"
            "'โปรแกรมที่ 1 หน่อยค่ะ' → [TOUR_DETAIL] country_id=2\n"
            "'อยากไปอิตาลี' → [SEARCH_TOURS] country_id=102\n"
            "'สนใจยุโรป' → [ASK_COUNTRY]\n"
            "'สวัสดี' หรือ 'มีทัวร์ไหม' → [ASK_COUNTRY]"
        ),
        messages=[{"role": "user", "content": user_message}],
    )
    return resp.content[0].text.strip()


def get_recommendations(user_message: str, tour_data: str) -> str:
    """Claude Round 2 — แนะนำ Top 3 ทัวร์ พร้อม link แต่ละโปรแกรม"""
    today = date.today().strftime("%-d %B %Y")
    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        system=(
            "คุณคือแอดมินรวมทัวร์ไฟไหม้ ตอบแบบ Messenger ไม่ใช่โบรชัวร์\n\n"
            f"วันนี้คือ {today}\n\n"
            "กฎการแสดงวันเดินทาง (สำคัญมาก):\n"
            "- มี ≤ 3 รอบ → แสดงแต่ละรอบแยกกัน เช่น 'เดินทาง 01-05 / 02-06 ก.ค. 69'\n"
            "- มี > 3 รอบ AND span ≤ 30 วัน → แสดงวันแรกและวันสุดท้าย เช่น 'เดินทาง 18-23 มิ.ย. 69'\n"
            "- มี > 3 รอบ AND span > 30 วัน → แสดงแค่เดือน เช่น 'เดินทาง เม.ย.-พ.ย. 69'\n"
            "- ถ้ารอบเดินทางผ่านวันนี้ไปแล้ว ให้ข้าม\n\n"
            "ข้อมูลทัวร์มี [LINK:URL] ต่อท้ายแต่ละโปรแกรม ให้นำ URL ตรงนั้นมาใส่เลย ห้ามเปลี่ยน\n\n"
            "รูปแบบที่ต้องตอบ (ห้ามเพิ่ม/ลดฟิลด์):\n\n"
            "มีแนะนำ 3 โปรแกรมค่ะ 😊\n\n"
            "1. [ชื่อทัวร์] [จำนวนวัน]\n"
            "✈️ [สายการบิน] | ราคาเริ่มต้น [XXX] บ.\n"
            "เดินทาง [วันหรือเดือนตามกฎด้านบน]\n"
            "🔗 [URL]\n\n"
            "2. [ชื่อทัวร์] [จำนวนวัน]\n"
            "✈️ [สายการบิน] | ราคาเริ่มต้น [XXX] บ.\n"
            "เดินทาง [วันหรือเดือน]\n"
            "🔗 [URL]\n\n"
            "3. [ชื่อทัวร์] [จำนวนวัน]\n"
            "✈️ [สายการบิน] | ราคาเริ่มต้น [XXX] บ.\n"
            "เดินทาง [วันหรือเดือน]\n"
            "🔗 [URL]\n\n"
            "[คำถาม 1 ข้อ เช่น 'ไปกี่คนคะ?']\n\n"
            "ห้ามเด็ดขาด: บรรยายสถานที่, ค่าทิป, ราคามัดจำ, ลิ้งค์รวม/ดูเพิ่มเติม"
        ),
        messages=[{
            "role": "user",
            "content": f"ลูกค้าถาม: {user_message}\n\nรายการทัวร์:\n{tour_data}",
        }],
    )
    return resp.content[0].text.strip()


def get_tour_detail(user_message: str, tour_data: str) -> str:
    """Claude Round 2B — รายละเอียดเต็มของ 1 ทัวร์ที่ลูกค้าเลือก"""
    today = date.today().strftime("%-d %B %Y")
    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=700,
        system=(
            "คุณคือแอดมินรวมทัวร์ไฟไหม้ ลูกค้าขอดูรายละเอียดเต็มของทัวร์ที่สนใจ\n\n"
            f"วันนี้คือ {today}\n\n"
            "เลือกโปรแกรมที่ตรงกับที่ลูกค้าถามมากที่สุด 1 โปรแกรม\n\n"
            "กฎวันเดินทาง:\n"
            "- มี ≤ 3 รอบ → แสดงแต่ละรอบแยกกัน เช่น '01-05 / 02-06 ก.ค. 69'\n"
            "- มี > 3 รอบ AND span ≤ 30 วัน → แสดงวันแรก-สุดท้าย เช่น '18-23 มิ.ย. 69'\n"
            "- มี > 3 รอบ AND span > 30 วัน → แสดงเดือน เช่น 'พ.ค.-ก.ค. 69' + '(สอบถามวันที่สะดวกได้ค่ะ)'\n"
            "- ข้ามรอบที่ผ่านวันนี้ไปแล้ว\n\n"
            "ข้อมูลทัวร์มี [LINK:URL] ให้นำ URL ตรงนั้นมาใส่เลย ห้ามเปลี่ยน\n\n"
            "รูปแบบที่ต้องตอบ:\n\n"
            "[ชื่อทัวร์] [จำนวนวัน]\n"
            "✈️ [สายการบิน] ([สนามบินออกเดินทาง])\n"
            "เดินทาง [วันหรือเดือนตามกฎ]\n"
            "ราคาเริ่มต้น [XXX] บ./ท่าน\n\n"
            "ไฮไลต์:\n"
            "- [สั้นๆ เช่น 'อิสระ 1 วันเต็ม']\n"
            "- [สั้นๆ เช่น 'พักออนเซ็น 1 คืน']\n"
            "- [สั้นๆ เช่น 'ไม่มีร้านช้อปบังคับ']\n\n"
            "[ถ้ามีค่าทิปในข้อมูล: 'หมายเหตุ: ไม่รวมค่าทิป XXX บ.']\n\n"
            "🔗 [URL]\n\n"
            "[คำถาม 1 ข้อ เช่น 'ไปกี่คนคะ?' หรือ 'มีวันไหนสะดวกคะ?']\n\n"
            "ห้าม: ใช้ * หรือ ** (bold), ราคาพักเดี่ยว"
        ),
        messages=[{
            "role": "user",
            "content": f"ลูกค้าถาม: {user_message}\n\nรายการทัวร์:\n{tour_data}",
        }],
    )
    return resp.content[0].text.strip()

# ─── Core message processing ────────────────────────────────────────────────────
def process_message(sender_id: str, text: str):
    """Main logic — รันใน background thread เพื่อไม่ให้ webhook timeout"""
    logger.info(f"Processing [{sender_id}]: {text[:80]}")
    try:
        signal = analyze_intent(text)
        logger.info(f"Signal: {signal}")

        if "[NOTIFY_ADMIN]" in signal:
            # Track A — ทัวร์ไฟไหม้ (flash sale)
            reply = (
                "ขอบคุณที่สนใจทัวร์ไฟไหม้นะคะ 🔥 "
                "เจ้าหน้าที่ได้รับข้อมูลแล้ว "
                "จะรีบติดต่อกลับภายใน 15 นาทีค่ะ 😊"
            )
            send_message(sender_id, reply)

        elif "[ASK_COUNTRY]" in signal:
            # Track C — ไม่ระบุประเทศ ถามกลับ
            reply = (
                "สวัสดีค่ะ ยินดีต้อนรับสู่รวมทัวร์ไฟไหม้ 🔥\n\n"
                "อยากไปประเทศไหนคะ? มีทัวร์หลายเส้นทางเลย เช่น\n"
                "🇯🇵 ญี่ปุ่น | 🇰🇷 เกาหลี | 🇨🇳 จีน\n"
                "🇻🇳 เวียดนาม | 🇭🇰 ฮ่องกง | 🇸🇬 สิงคโปร์\n"
                "🌍 ยุโรป (อิตาลี สวิต อังกฤษ ฯลฯ) | 🇹🇼 ไต้หวัน\n\n"
                "บอกประเทศที่สนใจได้เลยนะคะ 😊"
            )
            send_message(sender_id, reply)

        elif "[SEARCH_TOURS]" in signal:
            # Track B — ทัวร์ทั่วไป ระบุประเทศ
            match = re.search(r"country_id=(\d+)", signal)
            if not match:
                raise ValueError(f"Cannot parse country_id from: {signal}")

            country_id = match.group(1)
            country_name = COUNTRY_MAP.get(country_id, country_id)
            logger.info(f"Fetching tours: {country_name} (id={country_id})")

            tour_data = fetch_tours(country_id)
            reply = get_recommendations(text, tour_data)
            send_message(sender_id, reply)

        elif "[TOUR_DETAIL]" in signal:
            # Track B2 — ขอรายละเอียดโปรแกรมเดียว
            match = re.search(r"country_id=(\d+)", signal)
            if not match:
                raise ValueError(f"Cannot parse country_id from: {signal}")

            country_id = match.group(1)
            country_name = COUNTRY_MAP.get(country_id, country_id)
            logger.info(f"Tour detail: {country_name} (id={country_id})")

            tour_data = fetch_tours(country_id)
            reply = get_tour_detail(text, tour_data)
            send_message(sender_id, reply)

        else:
            # Fallback
            logger.warning(f"Unknown signal, using fallback: {signal}")
            send_message(sender_id, _fallback_msg())

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        try:
            send_message(sender_id, _fallback_msg())
        except Exception:
            pass


def _fallback_msg() -> str:
    return (
        "ขอบคุณที่ติดต่อรวมทัวร์ไฟไหม้นะคะ 🙏 "
        "ขณะนี้ทีมแอดมินจะติดต่อกลับหาคุณในเร็วๆ นี้ค่ะ 😊"
    )

# ─── Webhook routes ─────────────────────────────────────────────────────────────
@app.route("/webhook", methods=["GET"])
def verify():
    """Facebook webhook verification"""
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
    """รับ Facebook Messenger events"""
    data = request.get_json(silent=True)

    if not data or data.get("object") != "page":
        return jsonify({"status": "ignored"}), 200

    for entry in data.get("entry", []):
        for msg_event in entry.get("messaging", []):
            # Skip echoes (messages sent BY the page)
            if msg_event.get("message", {}).get("is_echo"):
                continue

            sender_id = msg_event.get("sender", {}).get("id")
            text      = msg_event.get("message", {}).get("text", "").strip()

            if not sender_id or not text:
                continue

            # ส่ง 200 กลับ FB ก่อน แล้วค่อย process ใน background
            t = threading.Thread(target=process_message, args=(sender_id, text))
            t.daemon = True
            t.start()

    return jsonify({"status": "ok"}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "TourFiremai AI Bot"}), 200


# ─── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
