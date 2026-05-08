"""
AI Tour Concierge v2 — รวมทัวร์ไฟไหม้
แอดมิน AI ค้นหาและแนะนำโปรแกรมทัวร์จากเว็บจริง พร้อม Conversation Memory
"""

import os
import re
import json
import logging
import threading
from datetime import date, datetime, timedelta
from flask import Flask, request, jsonify, abort
import anthropic
import requests
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
    "1": "เกาหลี", "2": "ญี่ปุ่น", "3": "ฮ่องกง",
    "4": "สิงคโปร์", "5": "จีน", "6": "มาเลเซีย",
    "7": "เวียดนาม", "19": "ไต้หวัน",
    "42": "อังกฤษ", "47": "สแกนดิเนเวีย", "64": "สวิตเซอร์แลนด์",
    "71": "ตุรเคีย", "80": "ยุโรปตะวันออก", "100": "เยอรมนี",
    "101": "ฝรั่งเศส", "102": "อิตาลี", "105": "สเปน",
    "159": "ออสเตรีย", "169": "กรีซ", "200": "โปรตุเกส",
}

# ─── Conversation Store ───────────────────────────────────────────────────────
# { psid: {"history": [...], "last_active": datetime} }
conversation_store: dict = {}
HISTORY_LIMIT  = 14      # messages kept per user (7 turns)
SESSION_TIMEOUT = timedelta(hours=2)

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

# ─── AI — System Prompt ───────────────────────────────────────────────────────
def _system_prompt() -> str:
    today = date.today().strftime("%-d %B %Y")
    return f"""คุณคือ "แอดมิน AI" ของเพจรวมทัวร์ไฟไหม้ — ผู้ช่วยค้นหาและแนะนำโปรแกรมทัวร์
บริษัท อัพ-โอเปอเรชั่น จำกัด | เว็บ: www.tourfiremai.com | LINE: @tourfiremai
วันนี้: {today}

## บุคลิก
- ฉลาด ชัดเจน ขายเป็น แต่ไม่แถ ไม่ยืนยันสิ่งที่ไม่รู้จริง
- เป็นกันเองและมืออาชีพ ใช้ "ค่ะ" และ "นะคะ"
- ไม่แอบเป็นมนุษย์ — บอกตรงๆ ว่าเป็น AI เมื่อถูกถาม
- ตอบกระชับ ตรงประเด็น ไม่เยิ่นเย้อ

## สิ่งที่ทำได้
- จำ context การสนทนาทั้งหมด — จำประเทศ เดือน จำนวนคน งบที่ลูกค้าบอก
- ถามข้อมูลที่ขาดอยู่อย่างเป็นธรรมชาติ (ไม่จำเป็นต้องถามครบทีเดียว)
- คัดโปรแกรม เปรียบเทียบตัวเลือก บอกเหตุผลว่าทำไมแนะนำ
- แนะนำประเทศอื่นถ้างบไม่พอ

## กฎการแสดงวันเดินทาง (สำคัญมาก)
- ≤ 3 รอบเดินทาง → แสดงแต่ละรอบแยก เช่น "01-05 / 08-12 ก.ค. 69"
- > 3 รอบ AND span ≤ 30 วัน → แสดงวันแรก-วันสุดท้าย เช่น "18-23 มิ.ย. 69"
- > 3 รอบ AND span > 30 วัน → แสดงเดือน เช่น "เม.ย.-พ.ย. 69"
- ข้ามรอบที่ผ่านวันนี้ไปแล้ว
- ถ้าไม่มีรอบที่เหลือเลย → บอกว่า "ขณะนี้ยังไม่มีรอบที่เปิด ทีมงานจะช่วยเช็กให้ค่ะ"

## รูปแบบตอบเมื่อมีข้อมูลทัวร์
เริ่มด้วยสรุปความต้องการสั้นๆ 1 บรรทัด แล้วเสนอ 1-3 ตัวเลือก:

[เลข]. [ชื่อทัวร์] [จำนวนวัน]
✈️ [สายการบิน] | ราคาเริ่มต้น [XXX] บ./ท่าน
เดินทาง [วันหรือเดือนตามกฎ]
👉 [เหตุผล 1 ประโยคว่าทำไมเหมาะ]
🔗 [URL จาก LINK: ในข้อมูล]

ลงท้ายด้วยคำถาม next step 1 ข้อ เช่น "ไปกี่คนคะ?" หรือ "มีวันไหนสะดวกคะ?"

ห้าม: บรรยายสถานที่, ค่าทิป, ค่ามัดจำ, ราคาพักเดี่ยว, ลิงก์รวม/หน้าเว็บทั่วไป
ห้าม: ยืนยันที่นั่งว่าง, ยืนยันราคา final, บอกชื่อ Wholesale, รับเงิน

## เมื่อไหร่ต้องส่งต่อทีมงาน
ลูกค้าพร้อมจอง / ถามที่นั่ง / ขอราคา final / ขอส่วนลด / ถามยกเลิกละเอียด / มีปัญหาหลังขาย
→ ตอบว่า: "ข้อมูลนี้มีผลกับการจองจริง แอดมิน AI ขอส่งให้ทีมงานเช็กและติดต่อกลับนะคะ 😊"
"""

# ─── AI — Call 1: Decide Action ───────────────────────────────────────────────
def decide_action(user_message: str, history: list) -> dict:
    """
    วิเคราะห์ว่าต้องทำอะไร คืน JSON:
    {"action": "search"|"detail"|"flash_sale"|"handoff"|"reply", "country_id": "2"|null}
    """
    history_text = ""
    for msg in history[-8:]:
        role = "ลูกค้า" if msg["role"] == "user" else "AI"
        history_text += f"{role}: {msg['content'][:200]}\n"

    prompt = (
        f"บทสนทนาที่ผ่านมา:\n{history_text}\n"
        f"ข้อความใหม่: {user_message}\n\n"
        "ตอบเป็น JSON เท่านั้น (ห้ามมีข้อความอื่น):\n"
        "{\"action\": \"search\" | \"detail\" | \"flash_sale\" | \"handoff\" | \"reply\",\n"
        " \"country_id\": \"เลขประเทศ หรือ null\"}\n\n"
        "action=search: ลูกค้าต้องการดูโปรแกรมทัวร์ประเทศที่ระบุได้\n"
        "action=detail: ลูกค้าขอดูรายละเอียดทัวร์โปรแกรมใดโปรแกรมหนึ่ง\n"
        "action=flash_sale: ลูกค้าถาม 'ทัวร์ไฟไหม้' หรือโปรโมชั่นพิเศษ/ดีลร้อน\n"
        "action=handoff: ลูกค้าพร้อมจอง/ถามที่นั่ง/ขอราคา final/ขอส่วนลด/ยกเลิก\n"
        "action=reply: ทักทาย/ถามทั่วไป/ยังไม่ระบุประเทศ/ถามเกี่ยวกับยุโรปโดยรวม\n\n"
        "Country IDs เอเชีย: ญี่ปุ่น=2, เกาหลี=1, เวียดนาม=7, จีน=5, ฮ่องกง=3, สิงคโปร์=4, มาเลเซีย=6, ไต้หวัน=19\n"
        "Country IDs ยุโรป: อิตาลี=102, สวิตเซอร์แลนด์=64, สแกนดิเนเวีย=47, อังกฤษ=42, เยอรมนี=100, ตุรเคีย=71, ออสเตรีย=159, สเปน=105, ฝรั่งเศส=101, กรีซ=169, โปรตุเกส=200, ยุโรปตะวันออก=80\n"
        "ถ้าพูดว่า 'ยุโรป' รวมๆ โดยไม่ระบุประเทศ → action=reply (AI จะถามประเทศ)"
    )

    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        m = re.search(r"\{.*?\}", raw, re.DOTALL)
        if m:
            data = json.loads(m.group())
            # Normalize
            data["action"] = data.get("action", "reply")
            cid = data.get("country_id")
            data["country_id"] = str(cid) if cid and str(cid) != "null" else None
            return data
    except Exception as e:
        logger.error(f"decide_action error: {e}")

    return {"action": "reply", "country_id": None}


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
        user_content = user_message

    messages.append({"role": "user", "content": user_content})

    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=700,
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
        action     = action_data.get("action", "reply")
        country_id = action_data.get("country_id")
        logger.info(f"Action: {action}, country_id: {country_id}")

        tour_data   = ""
        is_handoff  = False

        # Fetch tour data if needed
        if action in ("search", "detail") and country_id:
            country_name = COUNTRY_MAP.get(country_id, country_id)
            logger.info(f"Fetching tours: {country_name} (id={country_id})")
            try:
                tour_data = fetch_tours(country_id)
            except Exception as e:
                logger.error(f"fetch_tours error: {e}")
                tour_data = ""

        # Notify admin for flash_sale or handoff
        if action == "flash_sale":
            notify_telegram(
                f"🔥 ทัวร์ไฟไหม้!\nPSID: {sender_id}\nข้อความ: {text}"
            )
        elif action == "handoff":
            is_handoff = True
            notify_telegram(
                f"🔔 Lead ร้อน — ลูกค้าพร้อมจอง!\nPSID: {sender_id}\nข้อความ: {text}"
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
