"""
AI Sales Bot — รวมทัวร์ไฟไหม้
Facebook Messenger Webhook Server
"""

import os
import re
import json
import logging
import threading
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
    """ดึงข้อมูลทัวร์จาก tourfiremai.com แล้ว strip HTML"""
    url = f"https://www.tourfiremai.com/intertour/{country_id}/"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TourFiremaiBot/1.0)"}
    resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:6000]

# ─── Claude helpers ─────────────────────────────────────────────────────────────
def analyze_intent(user_message: str) -> str:
    """Claude Round 1 — จำแนก Track A (ทัวร์ไฟไหม้) หรือ Track B (ทัวร์ทั่วไป)"""
    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        system=(
            "คุณคือระบบ AI วิเคราะห์ข้อความลูกค้า Facebook Messenger สำหรับรวมทัวร์ไฟไหม้\n\n"
            "วิเคราะห์ข้อความและตอบในรูปแบบที่กำหนดเท่านั้น:\n\n"
            "Track A (ทัวร์ไฟไหม้): ตอบ [NOTIFY_ADMIN] ชื่อทัวร์=X, จำนวนคน=Y, วันที่=Z\n"
            "Track B (ทัวร์ทั่วไป): ตอบ [SEARCH_TOURS] country_id=X\n"
            "ID: ญี่ปุ่น=2, เกาหลี=1, เวียดนาม=7, จีน=5, ฮ่องกง=3, สิงคโปร์=4, มาเลเซีย=6, ไต้หวัน=19\n\n"
            "กฎ: ตอบเฉพาะ Signal Format เท่านั้น ไม่ต้องมีข้อความอื่น"
        ),
        messages=[{"role": "user", "content": user_message}],
    )
    return resp.content[0].text.strip()


def get_recommendations(user_message: str, tour_data: str) -> str:
    """Claude Round 2 — แนะนำ Top 3 ทัวร์"""
    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        system=(
            "คุณคือน้องแอดมินของรวมทัวร์ไฟไหม้ ตอบภาษาไทยแบบแชทสั้นๆ เป็นกันเอง น่ารัก ใช้ emoji พอประมาณ\n\n"
            "กฎเหล็ก:\n"
            "- แนะนำ Top 3 ทัวร์เท่านั้น ห้ามเกิน\n"
            "- แต่ละทัวร์ใช้ไม่เกิน 3 บรรทัด: ชื่อ + ราคา + วันเดินทาง\n"
            "- ลิงค์ 1 อัน ต่อท้าย 'ดูเพิ่มเติม: [link]'\n"
            "- ห้ามใส่ไฮไลต์ / รายละเอียดยาว / ราคามัดจำ / ค่าทิป\n"
            "- ปิดท้ายด้วยคำถาม 1 ข้อ เช่น 'อยากไปช่วงไหนคะ?' หรือ 'ไปกี่คนคะ?'\n"
            "- รวมทั้งหมดไม่เกิน 300 ตัวอักษร"
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
            # Track A — ทัวร์ไฟไหม้
            reply = (
                "ขอบคุณที่สนใจทัวร์ไฟไหม้นะคะ 🔥 "
                "เจ้าหน้าที่ได้รับข้อมูลแล้ว "
                "จะรีบติดต่อกลับภายใน 15 นาทีค่ะ 😊"
            )
            send_message(sender_id, reply)

        elif "[SEARCH_TOURS]" in signal:
            # Track B — ทัวร์ทั่วไป
            match = re.search(r"country_id=(\d+)", signal)
            if not match:
                raise ValueError(f"Cannot parse country_id from: {signal}")

            country_id = match.group(1)
            country_name = COUNTRY_MAP.get(country_id, country_id)
            logger.info(f"Fetching tours: {country_name} (id={country_id})")

            tour_data = fetch_tours(country_id)
            reply = get_recommendations(text, tour_data)
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


# ─── Entry point ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
