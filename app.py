"""
AI Tour Concierge v3 — รวมทัวร์ไฟไหม้
น้องแอดมิน AI ค้นหาและแนะนำโปรแกรมทัวร์จากเว็บจริง
พร้อม Conversation Memory (Redis) + Structured Context + Supabase CRM
"""

import os
import re
import json
import logging
import threading
from datetime import date, datetime, timedelta
from flask import Flask, request, jsonify, abort, render_template_string
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

VERIFY_TOKEN      = os.environ.get("VERIFY_TOKEN", "tourfiremai2024")
FB_PAGE_TOKEN     = os.environ.get("FB_PAGE_TOKEN", "")
ANTHROPIC_KEY     = os.environ.get("ANTHROPIC_API_KEY", "")
LINE_CHANNEL_TOKEN = os.environ.get("LINE_CHANNEL_TOKEN", "")   # LINE Messaging API — Channel Access Token
LINE_ADMIN_ID      = os.environ.get("LINE_ADMIN_ID", "")          # LINE User ID หรือ Group ID ของแอดมิน
SUPABASE_URL      = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY      = os.environ.get("SUPABASE_KEY", "")
DASHBOARD_PASS    = os.environ.get("DASHBOARD_PASSWORD", "tourfiremai2024")

claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ─── Country Map ─────────────────────────────────────────────────────────────
COUNTRY_MAP = {
    "1": "เกาหลี", "2": "ญี่ปุ่น", "3": "ฮ่องกง",
    "4": "สิงคโปร์", "5": "จีน", "6": "มาเลเซีย",
    "7": "เวียดนาม", "8": "พม่า", "9": "ลาว",
    "18": "อินโดนีเซีย", "19": "ไต้หวัน", "29": "มาเก๊า",
    "104": "ฟิลิปปินส์",
    "14": "อินเดีย", "20": "ภูฏาน", "182": "ศรีลังกา",
    "184": "ทิเบต", "173": "อุซเบกิสถาน", "256": "คาซัคสถาน",
    "164": "ปากีสถาน", "165": "มองโกเลีย",
    "72": "สหรัฐอาหรับฯ", "70": "จอร์แดน", "16": "อียิปต์",
    "167": "เคนย่า", "68": "แอฟริกาใต้", "161": "โมร็อกโก",
    "183": "อิหร่าน",
    "42": "อังกฤษ", "64": "สวิตเซอร์แลนด์", "100": "เยอรมนี",
    "101": "ฝรั่งเศส", "102": "อิตาลี", "105": "สเปน",
    "159": "ออสเตรีย", "169": "กรีซ", "200": "โปรตุเกส",
    "213": "เบลเยี่ยม", "308": "เนเธอร์แลนด์", "2217": "เบเนลักซ์",
    "47": "สแกนดิเนเวีย", "65": "ฟินแลนด์", "153": "สวีเดน",
    "162": "นอร์เวย์", "232": "เดนมาร์ก", "25": "ไอซ์แลนด์",
    "194": "ไอร์แลนด์", "197": "สกอตแลนด์",
    "80": "ยุโรปตะวันออก", "66": "โครเอเชีย", "166": "โปแลนด์",
    "168": "จอร์เจีย", "71": "ตุรเคีย", "2213": "บอลติก",
    "2220": "โรมาเนีย", "275": "มอลตา", "276": "มอนเตเนโกร",
    "10": "ออสเตรเลีย", "11": "นิวซีแลนด์",
    "12": "อเมริกา", "73": "แคนาดา", "174": "บราซิล",
    "175": "อาร์เจนติน่า", "226": "โคลอมเบีย", "272": "เม็กซิโก",
    "17": "รัสเซีย",
}

# ─── Redis setup (Upstash) ────────────────────────────────────────────────────
HISTORY_LIMIT   = 30
SESSION_TIMEOUT = timedelta(days=30)
REDIS_TTL_SEC   = 30 * 24 * 3600

REDIS_URL = os.environ.get("REDIS_URL", "")
_redis = None
if REDIS_URL:
    try:
        import redis as _redis_lib
        _redis = _redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=3)
        _redis.ping()
        logger.info("Redis connected ✅")
    except Exception as e:
        logger.warning(f"Redis unavailable, using in-memory fallback: {e}")
        _redis = None
else:
    logger.info("No REDIS_URL — using in-memory store")

_mem_store: dict = {}      # history fallback
_ctx_store: dict = {}      # context fallback


# ── History key & functions ───────────────────────────────────────────────────
def _redis_key(psid: str) -> str:
    return f"tourfiremai:chat:{psid}"

def get_history(psid: str) -> list:
    if _redis:
        try:
            raw = _redis.get(_redis_key(psid))
            if raw:
                conv = json.loads(raw)
                last = datetime.fromisoformat(conv.get("last_active", "2000-01-01T00:00:00"))
                if datetime.now() - last > SESSION_TIMEOUT:
                    _redis.delete(_redis_key(psid))
                    return []
                return conv.get("history", [])
            return []
        except Exception as e:
            logger.warning(f"Redis get error: {e}")

    if psid in _mem_store:
        conv = _mem_store[psid]
        if datetime.now() - conv["last_active"] > SESSION_TIMEOUT:
            del _mem_store[psid]
            return []
        return conv["history"]
    return []

def save_to_history(psid: str, role: str, content: str):
    history = get_history(psid)
    history.append({"role": role, "content": content})
    history = history[-HISTORY_LIMIT:]
    now_iso = datetime.now().isoformat()

    if _redis:
        try:
            conv = {"history": history, "last_active": now_iso}
            _redis.setex(_redis_key(psid), REDIS_TTL_SEC, json.dumps(conv, ensure_ascii=False))
            return
        except Exception as e:
            logger.warning(f"Redis set error: {e}")

    _mem_store[psid] = {"history": history, "last_active": datetime.now()}


# ── Structured Context key & functions ───────────────────────────────────────
def _ctx_key(psid: str) -> str:
    return f"tourfiremai:context:{psid}"

_EMPTY_CTX = {
    # ── Core customer info ─────────────────────────────────────────────────
    "customer_name": None,
    "phone": None,
    # ── Destination / Search ──────────────────────────────────────────────
    "destination": None,          # human text เช่น "ญี่ปุ่น โตเกียว"
    "destination_text": None,     # full text from user เช่น "อยากไปญี่ปุ่นช่วงซากุระ"
    "country": None,              # ชื่อประเทศ เช่น "ญี่ปุ่น"
    "country_id": None,           # รหัสประเทศ เช่น "2"
    "country_name": None,         # ชื่อประเทศแบบสมบูรณ์ (จาก classifier)
    "city_hint": None,            # เมือง/จังหวัดที่สนใจ เช่น "โอซาก้า"
    "inferred_destination": None, # ปลายทางที่คาดเดาจากโฆษณา
    "duration_days": None,        # จำนวนวัน เช่น 5
    "month": None,
    "budget_per_person": None,
    "pax": None,
    # ── Options & Selection ───────────────────────────────────────────────
    "last_options": [],           # list of tour dicts ที่เสนอล่าสุด
    "selected_tour": None,        # full dict ของทัวร์ที่เลือก {name, url, code, price, airline}
    "selected_tour_name": None,   # shortcut สำหรับ backward compat
    "selected_tour_url": None,
    # ── Booking flow ──────────────────────────────────────────────────────
    "lead_stage": None,           # cold/warm/hot/booking/paid/awaiting_docs/complete
    "travel_date": None,          # วันเดินทางที่ลูกค้าเลือก
    "payment_received": False,
    # ── Ad Attribution ────────────────────────────────────────────────────
    "entry_source": None,         # 'messenger','ad_click','referral'
    "ad_id": None,
    "ad_title": None,
    "ad_ref": None,
    "post_id": None,
    # ── Meta ──────────────────────────────────────────────────────────────
    "last_user_message": None,
    "last_bot_message": None,
    "pending_action": None,
    "updated_at": None,
}

def get_context(psid: str) -> dict:
    """คืน structured context ของลูกค้า"""
    if _redis:
        try:
            raw = _redis.get(_ctx_key(psid))
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.warning(f"Redis ctx get error: {e}")

    return dict(_ctx_store.get(psid, _EMPTY_CTX))

def save_context(psid: str, ctx: dict):
    """บันทึก structured context"""
    if _redis:
        try:
            _redis.setex(_ctx_key(psid), REDIS_TTL_SEC, json.dumps(ctx, ensure_ascii=False))
            return
        except Exception as e:
            logger.warning(f"Redis ctx set error: {e}")

    _ctx_store[psid] = ctx

def extract_context_after_response(psid: str, history: list, ai_response: str) -> dict:
    """ใช้ Haiku วิเคราะห์บทสนทนาและ extract structured context
    ทำงานแบบ async ใน background — ไม่บล็อก response หลัก"""
    existing = get_context(psid)

    history_text = ""
    for msg in history[-14:]:
        role = "ลูกค้า" if msg["role"] == "user" else "AI"
        history_text += f"{role}: {msg['content'][:250]}\n"
    history_text += f"AI: {ai_response[:300]}\n"

    prompt = (
        f"บทสนทนา:\n{history_text}\n\n"
        f"ข้อมูลที่มีอยู่แล้ว:\n"
        f"ชื่อลูกค้า: {existing.get('customer_name') or 'ไม่ทราบ'}\n"
        f"เบอร์โทร: {existing.get('phone') or 'ไม่ทราบ'}\n"
        f"ปลายทาง: {existing.get('destination') or 'ไม่ทราบ'}\n"
        f"ประเทศ: {existing.get('country') or 'ไม่ทราบ'}\n"
        f"เดือน: {existing.get('month') or 'ไม่ทราบ'}\n"
        f"งบ/คน: {existing.get('budget_per_person') or 'ไม่ทราบ'}\n"
        f"จำนวนคน: {existing.get('pax') or 'ไม่ทราบ'}\n"
        f"วันเดินทางที่เลือก: {existing.get('travel_date') or 'ยังไม่เลือก'}\n"
        f"โปรแกรมที่เลือก: {existing.get('selected_tour_name') or 'ยังไม่เลือก'}\n\n"
        "สกัดข้อมูลจากบทสนทนา ตอบเป็น JSON เท่านั้น (อัปเดตเฉพาะที่มีในบทสนทนา):\n"
        "{\n"
        '  "customer_name": "ชื่อลูกค้า หรือ null",\n'
        '  "phone": "เบอร์โทร/LINE หรือ null",\n'
        '  "destination": "เมือง/ปลายทาง หรือ null",\n'
        '  "country": "ชื่อประเทศ หรือ null",\n'
        '  "month": "เดือน+ปีที่จะไป เช่น ก.ค. 69 หรือ null",\n'
        '  "budget_per_person": งบประมาณต่อคนเป็นตัวเลขหรือnull,\n'
        '  "pax": จำนวนคนเป็นตัวเลขหรือnull,\n'
        '  "travel_date": "วันเดินทางที่ลูกค้าเลือก เช่น 5 มิ.ย. 69 หรือ null",\n'
        '  "selected_tour_name": "ชื่อโปรแกรมที่ลูกค้าเลือกชัดเจน หรือ null",\n'
        '  "last_options": [{"index":1,"code":"รหัส","name":"ชื่อโปรแกรม","url":"URL"}],\n'
        '  "pending_action": "สิ่งที่ AI บอกว่าจะทำต่อ เช่น search_osaka หรือ null"\n'
        "}\n"
        "หมายเหตุ:\n"
        "- last_options ให้ดึงจากข้อความล่าสุดที่ AI เสนอโปรแกรม (ไม่เกิน 3 ตัวเลือก)\n"
        "- travel_date: ดึงเมื่อลูกค้าบอกวันเดินทางชัดเจน\n"
        "- selected_tour_name: ดึงเมื่อลูกค้าเลือกโปรแกรมเฉพาะเจาะจงแล้ว"
    )

    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        m = re.search(r"\{.*?\}", raw, re.DOTALL)
        if m:
            new_ctx = json.loads(m.group())
            # Merge: keep existing values if new is null
            merged = dict(existing)
            for key, val in new_ctx.items():
                if val is not None and val != [] and val != "null":
                    merged[key] = val
            # Preserve payment_received flag — never overwrite with False
            if existing.get("payment_received"):
                merged["payment_received"] = True
            merged["last_bot_message"] = ai_response[:600]
            merged["updated_at"] = datetime.now().isoformat()
            return merged
    except Exception as e:
        logger.error(f"extract_context error: {e}")

    existing["last_bot_message"] = ai_response[:600]
    existing["updated_at"] = datetime.now().isoformat()
    return existing


# ─── Supabase CRM ─────────────────────────────────────────────────────────────
def _sb_headers(prefer_upsert: bool = False) -> dict:
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer_upsert:
        h["Prefer"] = "resolution=merge-duplicates,return=minimal"
    else:
        h["Prefer"] = "return=minimal"
    return h

def save_lead_supabase(psid: str, context: dict, lead_stage: str, user_message: str = ""):
    """Upsert lead ลงใน Supabase โดยใช้ psid เป็น unique key"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.info("Supabase not configured — skip lead save")
        return
    try:
        url = f"{SUPABASE_URL}/rest/v1/leads"
        payload = {
            "psid": psid,
            "lead_stage": lead_stage,
            "updated_at": datetime.now().isoformat(),
        }
        # เพิ่มเฉพาะ field ที่มีข้อมูล
        field_map = {
            "customer_name": "customer_name",
            "phone": "phone",
            "destination": "destination",
            "country": "country",
            "month": "month",
            "budget_per_person": "budget_per_person",
            "pax": "pax",
            "travel_date": "travel_date",
            "selected_tour_name": "selected_tour_name",
        }
        for ctx_key, db_col in field_map.items():
            v = context.get(ctx_key)
            if v is not None:
                payload[db_col] = v

        opts = context.get("last_options", [])
        if opts:
            payload["last_options"] = json.dumps(opts, ensure_ascii=False)

        if user_message:
            payload["last_message"] = user_message[:500]

        resp = requests.post(url, json=payload, headers=_sb_headers(prefer_upsert=True), timeout=10)
        if resp.ok or resp.status_code == 201:
            logger.info(f"✅ Lead upserted: {psid} [{lead_stage}]")
        else:
            logger.error(f"❌ Supabase {resp.status_code}: {resp.text[:300]}")
    except Exception as e:
        logger.error(f"save_lead_supabase error: {e}")

def list_leads_supabase(stage_filter: str = None, limit: int = 50) -> list:
    """ดึง leads จาก Supabase สำหรับ Dashboard"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        url = f"{SUPABASE_URL}/rest/v1/leads"
        params = {"order": "updated_at.desc", "limit": str(limit)}
        if stage_filter:
            params["lead_stage"] = f"eq.{stage_filter}"
        resp = requests.get(url, params=params, headers=_sb_headers(), timeout=10)
        if resp.ok:
            return resp.json()
        logger.error(f"list_leads error: {resp.status_code} {resp.text[:200]}")
        return []
    except Exception as e:
        logger.error(f"list_leads_supabase error: {e}")
        return []

def count_leads_by_stage() -> dict:
    """นับ leads แต่ละ stage"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {}
    stages = ["cold", "warm", "hot", "booking", "paid", "awaiting_docs", "complete"]
    counts = {}
    for s in stages:
        try:
            url = f"{SUPABASE_URL}/rest/v1/leads"
            headers = dict(_sb_headers())
            headers["Prefer"] = "count=exact"
            resp = requests.get(
                url,
                params={"lead_stage": f"eq.{s}", "select": "psid"},
                headers=headers,
                timeout=8
            )
            cr = resp.headers.get("Content-Range", "0/0")
            total = cr.split("/")[-1]
            counts[s] = int(total) if total.isdigit() else 0
        except Exception:
            counts[s] = 0
    return counts


# ─── Facebook helpers ─────────────────────────────────────────────────────────
def send_message(recipient_id: str, text: str):
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
                logger.info(f"✅ Sent ({len(chunk)} chars) to {recipient_id}")
            else:
                logger.error(f"❌ FB send error {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"❌ FB send exception: {e}")

def notify_line(message: str):
    """ส่งแจ้งเตือนผ่าน LINE Messaging API (push message)"""
    if not LINE_CHANNEL_TOKEN or not LINE_ADMIN_ID:
        logger.warning("LINE_CHANNEL_TOKEN / LINE_ADMIN_ID not set — skip notify")
        return
    try:
        requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "to": LINE_ADMIN_ID,
                "messages": [{"type": "text", "text": message}],
            },
            timeout=10,
        )
        logger.info("📨 LINE push sent")
    except Exception as e:
        logger.error(f"notify_line error: {e}")


# ─── Tour data fetcher ────────────────────────────────────────────────────────
def capture_ad_attribution(psid: str, msg_event: dict):
    """ดึงข้อมูล Ad Attribution จาก FB webhook event และบันทึก context + Supabase"""
    referral = (
        msg_event.get("referral") or
        msg_event.get("postback", {}).get("referral") or
        {}
    )
    ads_ctx = referral.get("ads_context_data") or msg_event.get("ads_context_data") or {}

    ad_id        = str(referral.get("ad_id") or ads_ctx.get("ad_id") or "")
    ad_title     = str(ads_ctx.get("ad_title") or "")
    adset_id     = str(ads_ctx.get("adset_id") or "")
    campaign_id  = str(ads_ctx.get("campaign_id") or "")
    ad_ref       = str(referral.get("ref") or "")
    post_id      = str(ads_ctx.get("photo_id") or referral.get("item") or "")
    source       = str(referral.get("source") or "")
    ref_type     = str(referral.get("type") or "")

    if not any([ad_id, ad_ref, post_id, campaign_id]):
        return  # ไม่มี ad data — ข้าม

    logger.info(f"🎯 Ad attribution: psid={psid} ad_id={ad_id} ref={ad_ref}")

    # อัพเดท context
    ctx = get_context(psid)
    if not ctx.get("ad_id") and ad_id:
        ctx["ad_id"] = ad_id
    if not ctx.get("ad_title") and ad_title:
        ctx["ad_title"] = ad_title
    if not ctx.get("ad_ref") and ad_ref:
        ctx["ad_ref"] = ad_ref
    if not ctx.get("post_id") and post_id:
        ctx["post_id"] = post_id
    if not ctx.get("entry_source"):
        ctx["entry_source"] = "ad_click" if ad_id else "referral"

    # Infer destination จาก ad_title (ถ้ามี keyword ประเทศ)
    if ad_title and not ctx.get("inferred_destination"):
        for keyword in ["ญี่ปุ่น","เกาหลี","จีน","เวียดนาม","ยุโรป","ฮ่องกง","สิงคโปร์","ไต้หวัน","ออสเตรเลีย"]:
            if keyword in ad_title:
                ctx["inferred_destination"] = keyword
                break
    save_context(psid, ctx)

    # บันทึก Supabase
    save_ad_attribution_supabase(psid, {
        "ad_id": ad_id, "ad_title": ad_title, "adset_id": adset_id,
        "campaign_id": campaign_id, "ad_ref": ad_ref, "post_id": post_id,
        "source": source, "type": ref_type,
        "raw_referral": referral,
    })


def save_ad_attribution_supabase(psid: str, ad_data: dict):
    """บันทึก ad attribution ลง Supabase"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        import json as _json
        payload = {"psid": psid}
        for k in ["ad_id","ad_title","adset_id","campaign_id","ad_ref","post_id","source","type"]:
            v = ad_data.get(k, "")
            if v:
                payload[k] = v
        raw = ad_data.get("raw_referral")
        if raw:
            payload["raw_referral"] = _json.dumps(raw, ensure_ascii=False)
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/ad_attributions",
            json=payload,
            headers=_sb_headers(),
            timeout=10
        )
        if resp.ok or resp.status_code == 201:
            logger.info(f"✅ Ad attribution saved for {psid}")
        else:
            logger.warning(f"Ad attribution save: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.error(f"save_ad_attribution_supabase error: {e}")


def save_customers_supabase(psid: str, ctx: dict):
    """Upsert ลูกค้าลง customers table"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    if not ctx.get("customer_name") and not ctx.get("phone"):
        return  # ไม่มีข้อมูลพอ
    try:
        payload = {"psid": psid}
        if ctx.get("customer_name"):
            payload["name"] = ctx["customer_name"]
        if ctx.get("phone"):
            payload["phone"] = ctx["phone"]
        if ctx.get("entry_source"):
            payload["entry_source"] = ctx["entry_source"]
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/customers",
            json=payload,
            headers=_sb_headers(prefer_upsert=True),
            timeout=10
        )
        if resp.ok or resp.status_code == 201:
            logger.info(f"✅ Customer upserted: {psid}")
        else:
            logger.warning(f"save_customers: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.error(f"save_customers_supabase error: {e}")


def _parse_listing_cards(html_text: str) -> list:
    """Parse tour cards (name + link) from a listing page."""
    soup = BeautifulSoup(html_text, "html.parser")
    cards = []
    for name_div in soup.find_all("div", class_="b-name"):
        h3 = name_div.find("h3")
        if not h3:
            continue
        name = h3.get_text(strip=True)
        link = ""
        # Walk ancestors to find the nearest /tour/ap* link
        for ancestor in name_div.parents:
            a = ancestor.find("a", href=re.compile(r"/tour/ap\w+"))
            if a:
                href = a["href"]
                link = ("https://www.tourfiremai.com" + href
                        if href.startswith("/") else href)
                break
            if ancestor.name in ("section", "body", "[document]"):
                break
        cards.append({"name": name, "link": link})
    return cards


def fetch_tour_detail_dates(tour_url: str) -> dict:
    """ดึง วันเดินทาง + ราคา + สายการบิน จาก detail page"""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TourFiremaiBot/1.0)"}
    resp = requests.get(tour_url, headers=headers, timeout=12)
    resp.raise_for_status()
    text = resp.text

    # Dates (Thai abbreviated months)
    raw_dates = re.findall(
        r"\d+\s*(?:ม\.ค|ก\.พ|มี\.ค|เม\.ย|พ\.ค|มิ\.ย|ก\.ค|ส\.ค|ก\.ย|ต\.ค|พ\.ย|ธ\.ค)\.?\s*\d*",
        text,
    )
    seen, unique_dates = set(), []
    for d in raw_dates:
        d = d.strip()
        if d not in seen:
            seen.add(d)
            unique_dates.append(d)
    dates_str = ", ".join(unique_dates[:8]) if unique_dates else "ติดต่อเช็กวัน"

    # Price
    prices = re.findall(r"[\d,]+\s*บาท", text)
    price_str = prices[0] if prices else "ติดต่อสอบถาม"

    # Airline code
    airlines = re.findall(r"\b(XJ|VZ|SL|FD|DD|TG|WE|PG|QR|TK|EK|MH|CX|XI|PR|OZ|KE)\b", text)
    airline_str = airlines[0] if airlines else ""

    return {"dates": dates_str, "price": price_str, "airline": airline_str}





def fetch_tour_detail_full(tour_url: str) -> str:
    """ดึงข้อมูลครบจาก HTML detail page — ทิป มัดจำ วีซ่า พักเดี่ยว รวม/ไม่รวม
    ใช้ก่อน fetch_pdf_info เพราะเร็วกว่าและมักมีข้อมูลหลักครบ
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; TourFiremaiBot/1.0)"}
        resp = requests.get(tour_url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # ลบ script/style/nav/footer ทิ้ง
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        full_text = soup.get_text(separator="\n")
        full_text = re.sub(r"\n{3,}", "\n\n", full_text).strip()

        # ดึงเฉพาะส่วนที่มี keyword สำคัญ
        keywords = [
            "มัดจำ", "ค่ามัดจำ", "deposit",
            "ทิป", "tip", "ค่าทิป",
            "วีซ่า", "visa",
            "พักเดี่ยว", "single",
            "รวม", "ไม่รวม", "อัตราค่าบริการ",
            "เงื่อนไข", "ยกเลิก", "cancel",
            "เด็ก", "ทารก",
        ]

        lines = full_text.split("\n")
        relevant_lines = []
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            if not line_stripped:
                continue
            if any(kw.lower() in line_stripped.lower() for kw in keywords):
                # เก็บ context รอบๆ ด้วย (บรรทัดก่อน+หลัง 2 บรรทัด)
                start = max(0, i - 2)
                end = min(len(lines), i + 3)
                chunk = "\n".join(lines[start:end]).strip()
                if chunk not in relevant_lines:
                    relevant_lines.append(chunk)

        if relevant_lines:
            result = "[ข้อมูลสำคัญจากหน้าโปรแกรม]\n" + "\n---\n".join(relevant_lines[:20])
            return result[:4000]

        # fallback: คืน text ทั้งหมดถ้าหา keyword ไม่เจอ
        return "[ข้อมูลหน้าโปรแกรม]\n" + full_text[:3000]

    except Exception as e:
        logger.error(f"fetch_tour_detail_full error {tour_url}: {e}")
        return ""

def fetch_tours_from_db(country_id: str, city_hint: str = None,
                        budget_max: int = None) -> list:
    """Query Supabase `tours` table.
    Returns list of dicts: name, url, tour_code, price_min, airline, departure_dates
    Filters by city and/or budget if provided.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        params = {
            "country_id": f"eq.{country_id}",
            "select":     "name,url,tour_code,price_min,airline,departure_dates",
            "order":      "price_min.asc.nullslast",
            "limit":      "60",
        }
        if city_hint:
            params["name"] = f"ilike.*{city_hint}*"
        if budget_max:
            params["price_min"] = f"lte.{budget_max}"
        headers = {
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        }
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/tours",
            params=params,
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
        logger.warning(f"fetch_tours_from_db: HTTP {resp.status_code} — {resp.text[:200]}")
        return []
    except Exception as e:
        logger.error(f"fetch_tours_from_db error: {e}")
        return []

def fetch_tours(country_id: str, city_hint: str = None, budget_max: int = None) -> str:
    """ดึงทัวร์ — ลอง Supabase DB ก่อน, fallback to web scraping

    DB path  (fast ~0.1s): query tours table → fetch detail pages for top 4
    Web path (slow ~4-8s): scan listing pages → fetch detail pages for top 4
    """
    from concurrent.futures import ThreadPoolExecutor

    def _enrich_and_format(cards: list, url_key: str = "url", max_detail: int = 4):
        """Fetch detail pages for top N cards and format output. Returns (str, list[dict])."""
        def _get_detail(t):
            link = t.get(url_key) or t.get("link") or t.get("url", "")
            if link:
                try:
                    t.update(fetch_tour_detail_dates(link))
                except Exception:
                    pass
            return t

        with ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(_get_detail, cards[:max_detail]))

        parts = []
        meta = []
        for i, t in enumerate(results):
            link = t.get(url_key) or t.get("link") or t.get("url", "")
            line = f"📌 {t['name']}"
            if t.get("airline"):
                line += f" ({t['airline']})"
            if t.get("dates"):
                line += f"\n   วันเดินทาง: {t['dates']}"
            if t.get("price"):
                line += f"\n   ราคาเริ่ม: {t['price']}"
            if link:
                line += f"\n   [LINK:{link}]"
            parts.append(line)
            meta.append({
                "index": i + 1,
                "tour_code": t.get("tour_code", ""),
                "name": t["name"],
                "price_min": t.get("price_min"),
                "url": link,
            })
        return "\n\n".join(parts), meta

    # ── 1. Try Supabase DB ────────────────────────────────────────────────────
    db_tours = fetch_tours_from_db(country_id, city_hint=city_hint, budget_max=budget_max)
    if db_tours:
        logger.info(f"DB hit: {len(db_tours)} tours for country={country_id} city={city_hint}")
        # Check if DB has price/dates data (v2 scraper)
        has_price_data = any(t.get("price_min") for t in db_tours)
        if has_price_data:
            # Format directly from DB — no detail page fetching needed
            parts = []
            meta = []
            for i, t in enumerate(db_tours[:6]):
                price_str = f"{t['price_min']:,} บาท" if t.get("price_min") else "ติดต่อสอบถาม"
                dates_str = t.get("departure_dates") or "ติดต่อเช็กวัน"
                airline_str = t.get("airline") or ""
                line = f"📌 {t['name']}"
                if airline_str:
                    line += f" ({airline_str})"
                line += f"\n   💰 ราคาเริ่ม: {price_str}"
                line += f"\n   📅 วันเดินทาง: {dates_str}"
                line += f"\n   🔗 [LINK:{t['url']}]"
                parts.append(line)
                meta.append({
                    "index": i + 1,
                    "tour_code": t.get("tour_code", ""),
                    "name": t["name"],
                    "price_min": t.get("price_min"),
                    "url": t.get("url", ""),
                })
            return "\n\n".join(parts), meta
        # DB has no price data yet → fallback to detail fetch
        result_str, result_meta = _enrich_and_format(db_tours, url_key="url", max_detail=12)
        return result_str, result_meta

    # ── 2. DB miss → fall back to web scraping ────────────────────────────────
    logger.info(f"DB miss for country={country_id} city={city_hint} → scraping web")
    base_url = f"https://www.tourfiremai.com/intertour/{country_id}/"
    headers  = {"User-Agent": "Mozilla/5.0 (compatible; TourFiremaiBot/1.0)"}

    if city_hint:
        # Smart mode: scan multiple pages, filter by city name
        matched_cards = []
        for page_num in range(1, 5):
            url = base_url if page_num == 1 else f"{base_url}?page={page_num}"
            try:
                resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
                resp.raise_for_status()
            except Exception:
                break
            cards = _parse_listing_cards(resp.text)
            if not cards:
                break
            matched_cards.extend([c for c in cards if city_hint in c["name"]])
            if len(matched_cards) >= 3:
                break

        if matched_cards:
            return _enrich_and_format(matched_cards, url_key="link")

    # Raw-text fallback (no city filter or no city matches found)
    try:
        resp = requests.get(base_url, headers=headers, timeout=20, allow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        raise

    soup = BeautifulSoup(resp.text, "html.parser")
    base = "https://www.tourfiremai.com"
    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "").strip()
        if not href or href.startswith("#") or href.startswith("javascript"):
            a_tag.replace_with(a_tag.get_text(strip=True))
            continue
        full_url = (base + href) if href.startswith("/") else href
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

_PDF_CACHE: dict = {}

def extract_program_url_from_history(history: list) -> str | None:
    pattern = re.compile(r'https://www\.tourfiremai\.com/tour/ap\w+')
    for msg in reversed(history):
        m = pattern.search(msg.get("content", ""))
        if m:
            return m.group(0)
    return None

def fetch_pdf_info(program_url: str) -> str:
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
            important_page_nums = list(range(max(0, doc.page_count - 5), doc.page_count))

        text_result = ""
        image_needed_pages = []

        for i in important_page_nums[:6]:
            t = page_texts.get(i, "")
            if len(t) > 80:
                text_result += f"\n[หน้า {i+1}]\n{t[:1500]}"
            else:
                image_needed_pages.append(i)

        if image_needed_pages:
            logger.info(f"PDF vision fallback for pages: {[p+1 for p in image_needed_pages]}")
            vision_result = _read_pdf_pages_with_vision(doc, image_needed_pages[:4], tour_id)
            if vision_result:
                text_result += f"\n[Vision OCR]\n{vision_result}"

        doc.close()

        result = f"[ข้อมูลจาก PDF โปรแกรม {tour_id}]\n{text_result.strip()}"
        result = result[:5000]
        _PDF_CACHE[program_url] = result
        logger.info(f"PDF fetched: {pdf_url} ({len(result)} chars)")
        return result

    except Exception as e:
        logger.error(f"fetch_pdf_info error: {e}")
        return ""

def _read_pdf_pages_with_vision(doc: fitz.Document, page_nums: list, tour_id: str) -> str:
    try:
        content = []
        for i in page_nums:
            page = doc[i]
            mat = fitz.Matrix(1.5, 1.5)
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
def _system_prompt(ctx: dict = None) -> str:
    today = date.today().strftime("%-d %B %Y")

    # Inject structured context memory if available
    ctx_section = ""
    if ctx:
        parts = []
        if ctx.get("customer_name"):
            parts.append(f"ชื่อลูกค้า: {ctx['customer_name']}")
        if ctx.get("phone"):
            parts.append(f"เบอร์/LINE: {ctx['phone']}")
        if ctx.get("destination"):
            parts.append(f"ปลายทาง: {ctx['destination']}")
        if ctx.get("country"):
            parts.append(f"ประเทศ: {ctx['country']}")
        if ctx.get("month"):
            parts.append(f"เดือนที่จะไป: {ctx['month']}")
        if ctx.get("budget_per_person"):
            parts.append(f"งบ/คน: {ctx['budget_per_person']:,} บาท" if isinstance(ctx['budget_per_person'], (int, float)) else f"งบ/คน: {ctx['budget_per_person']}")
        if ctx.get("pax"):
            parts.append(f"จำนวนคน: {ctx['pax']} คน")
        if ctx.get("travel_date"):
            parts.append(f"วันเดินทางที่เลือก: {ctx['travel_date']}")
        if ctx.get("selected_tour_name"):
            parts.append(f"โปรแกรมที่เลือก: {ctx['selected_tour_name']}")
        if ctx.get("last_options"):
            opts = ctx["last_options"]
            if isinstance(opts, str):
                try:
                    opts = json.loads(opts)
                except Exception:
                    opts = []
            if opts:
                opts_text = ", ".join([f"ตัวที่{o.get('index','')} {o.get('name','')}" for o in opts[:3]])
                parts.append(f"โปรแกรมที่เสนอล่าสุด: {opts_text}")
        if ctx.get("lead_stage"):
            parts.append(f"สถานะ lead: {ctx['lead_stage']}")
        if ctx.get("payment_received"):
            parts.append(f"ชำระเงินแล้ว: ✅")
        if ctx.get("pending_action"):
            parts.append(f"สิ่งที่ AI บอกว่าจะทำ: {ctx['pending_action']}")

        # Build explicit "known = don't ask" rules
        known_rules = []
        if ctx.get("month"):
            known_rules.append(f"❌ ห้ามถามเดือนเดินทาง — ทราบแล้วว่า {ctx['month']}")
        if ctx.get("travel_date"):
            known_rules.append(f"❌ ห้ามถามวันเดินทาง — ลูกค้าเลือกแล้ว {ctx['travel_date']}")
        if ctx.get("pax"):
            known_rules.append(f"❌ ห้ามถามจำนวนคน — ทราบแล้วว่า {ctx['pax']} คน")
        if ctx.get("budget_per_person"):
            known_rules.append("❌ ห้ามถามงบประมาณ — ทราบแล้ว")
        if ctx.get("customer_name"):
            known_rules.append(f"❌ ห้ามถามชื่อลูกค้า — ทราบแล้วว่า {ctx['customer_name']}")
        if ctx.get("phone"):
            known_rules.append("❌ ห้ามถามเบอร์/LINE — ทราบแล้ว")

        # Determine next step hint
        next_step = ""
        stage = ctx.get("lead_stage", "cold")
        has_pax = bool(ctx.get("pax"))
        has_month = bool(ctx.get("month"))
        has_tour = bool(ctx.get("selected_tour_name") or ctx.get("last_options"))
        has_name = bool(ctx.get("customer_name"))
        has_phone = bool(ctx.get("phone"))
        has_date = bool(ctx.get("travel_date"))

        if stage in ("booking", "paid", "awaiting_docs", "complete"):
            if ctx.get("payment_received"):
                next_step = "→ ลูกค้าชำระเงินแล้ว รอทีมงานยืนยันและส่งใบนัดหมาย"
            elif has_name and has_phone:
                next_step = "→ มีชื่อ+เบอร์แล้ว รอทีมงานยืนยันที่นั่งและแจ้งบัญชีชำระเงิน"
            else:
                next_step = "→ step ถัดไป: ขอชื่อ+เบอร์ เพื่อส่งทีมงาน"
        elif stage in ("warm", "hot") and not ctx.get("city_hint") and not has_tour:
            # ถ้ายังไม่รู้เมือง และยังไม่ได้เสนอทัวร์
            next_step = "→ step ถัดไป: ถามเมือง/เส้นทางที่สนใจ (STEP 0)"
        elif stage == "hot" and has_tour:
            if not has_month:
                next_step = "→ step ถัดไป: ถามเดือน/ช่วงที่สะดวกเดินทาง"
            elif not has_date:
                next_step = "→ step ถัดไป: ให้ลูกค้าเลือกวันเดินทางจากรอบที่มีในโปรแกรม"
            elif not has_pax:
                next_step = "→ step ถัดไป: ถามจำนวนผู้เดินทาง"
            elif not has_name or not has_phone:
                next_step = "→ step ถัดไป: ขอชื่อ+เบอร์/LINE ติดต่อ"
            elif has_name and has_phone:
                next_step = "→ step ถัดไป: สรุปการจองครบถ้วน แล้วส่งทีมงาน"

        # ── AD CONTEXT section ──────────────────────────────────────────────
        ad_parts = []
        if ctx.get("ad_id"):
            ad_parts.append(f"Ad ID: {ctx['ad_id']}")
        if ctx.get("ad_title"):
            ad_parts.append(f"ชื่อโฆษณา: {ctx['ad_title']}")
        if ctx.get("ad_ref"):
            ad_parts.append(f"Ref: {ctx['ad_ref']}")
        if ctx.get("inferred_destination"):
            ad_parts.append(f"ปลายทางที่โฆษณาชี้: {ctx['inferred_destination']}")
        if ctx.get("entry_source"):
            ad_parts.append(f"ที่มา: {ctx['entry_source']}")

        ad_section = ""
        if ad_parts:
            ad_block = "\n".join(f"  {p}" for p in ad_parts)
            inferred = ctx.get("inferred_destination", "")
            skip_ask = "→ ❌ ห้ามถาม 'อยากไปไหน' — ลูกค้ามาจากโฆษณาที่เกี่ยวกับ " + inferred if inferred else ""
            ad_section = (
                "\n══════════════════════════════════\n"
                "📣 ข้อมูล Ad Attribution (ที่มาลูกค้า)\n"
                "══════════════════════════════════\n"
                f"{ad_block}\n"
                f"{skip_ask}\n"
            )

        if parts:
            ctx_block = "\n".join(f"- {p}" for p in parts)
            rules_block = ("\n" + "\n".join(known_rules)) if known_rules else ""
            next_block = f"\n\n⏭ NEXT STEP: {next_step}" if next_step else ""
            ctx_section = (
                "\n══════════════════════════════════\n"
                "ข้อมูลลูกค้าที่จำไว้ (ใช้เป็น context หลัก)\n"
                "══════════════════════════════════\n"
                f"{ctx_block}"
                f"{rules_block}"
                f"{next_block}"
                "\n\nใช้ข้อมูลนี้ตอบได้เลย ห้ามถามข้อมูลที่ทราบแล้วซ้ำ\n"
                f"{ad_section}"
            )

    return f"""คุณคือ "น้องแอดมิน AI" ของเพจ รวมทัวร์ไฟไหม้ — ผู้ช่วยขายทัวร์ผู้หญิง ฉลาด อบอุ่น และขายเป็น
บริษัท อัพ-โอเปอเรชั่น จำกัด | เว็บ: www.tourfiremai.com | LINE: @tourfiremai
วันนี้: {today}
{ctx_section}
══════════════════════════════════
บุคลิกและสไตล์การตอบ — น้องแอดมิน AI
══════════════════════════════════
ชื่อ: น้องแอดมิน AI | เพศ: หญิง | สไตล์: อบอุ่น เป็นกันเอง ขายเก่ง

- พูดภาษาไทยสวย เป็นกันเองแต่มืออาชีพ ลงท้ายด้วย ค่ะ / นะคะ / คะ เสมอ
- ❌ ห้ามใช้คำลงท้าย "ครับ" เด็ดขาด — น้องแอดมิน AI เป็นผู้หญิง ใช้เฉพาะ ค่ะ / คะ / นะคะ / จ้ะ เท่านั้น
- ❌ ห้ามพูด "ยินดีต้อนรับครับ" หรือประโยคใดที่ลงท้ายด้วย ครับ ทุกกรณี
- อบอุ่น ยิ้มแย้ม เหมือนเพื่อนที่รู้เรื่องทัวร์ดี — ไม่เป็นทางการเกินไป ไม่แข็งกระด้าง
- ฉลาด ชัดเจน ขายเป็น ไม่แถ — ตอบตรงประเด็น ไม่เขียนเรียงความ
- ใช้ emoji ได้เต็มที่ในส่วนแนะนำทัวร์ (✈️ 📌 💰 📅 🔗 💡 🔥 💳 🤝 🛏 😊 🥰)
  ข้อความบทสนทนาทั่วไปใช้ไม่เกิน 1-2 ตัว
- ตอบสั้นกระชับ ถ้าข้อมูลยาวแบ่งเป็นบรรทัด อย่าบล็อกยาวต่อเนื่อง
- ถามทีละ 1 คำถามเท่านั้น เลือกสิ่งสำคัญที่สุดก่อน ห้ามถามหลายอย่างพร้อมกัน
- ถ้าลูกค้าบอกงบหรือปลายทางแล้ว → เสนอโปรแกรมก่อน แล้วค่อยถามต่อ อย่าถามก่อนโดยไม่เสนออะไร
- ถ้าลูกค้าดูลังเล → ให้กำลังใจเบาๆ เช่น "ไม่ต้องรีบตัดสินใจนะคะ ถามได้เลยค่ะ 😊"

══════════════════════════════════
กฎความจำบริบทและตัวเลือกที่เสนอ
══════════════════════════════════
จำข้อมูลสำคัญจากบทสนทนาปัจจุบันเสมอ:
- ประเทศ/เมือง/แนวทริป
- เดือนหรือวันที่เดินทาง
- จำนวนคน และงบประมาณ
- โปรแกรมที่เคยเสนอ — ตัวเลือก 1-3 ล่าสุดที่ AI เสนอ

ถ้าลูกค้าพิมพ์ว่า "ตัวที่ 1" / "ตัวที่ 2" / "ตัวที่ 3" / "อันนี้" / "ตัวนี้" / "เช็กเลย" / "สนใจอันนี้"
→ ให้ถือว่าหมายถึงโปรแกรมจากชุดที่ AI เสนอไว้ล่าสุด (ค้นจาก conversation history ถ้าจำไม่ได้)
→ ห้ามถามซ้ำว่าอยากไปประเทศไหน
→ ห้ามบอกว่า "บทสนทนาเพิ่งเริ่มต้น" หรือ "ยังไม่มีโปรแกรมที่เสนอ" — ให้ค้นใน history ก่อนเสมอ
→ ถ้าหา "ตัวที่ X" จาก history ได้ → แสดงรายละเอียดโปรแกรมนั้นทันที
→ ถ้าหาจาก history ไม่ได้จริงๆ → ถามประเทศ/ปลายทาง อย่าบอกว่าไม่มีข้อมูล

⚠️ กฎ single-option (สำคัญมาก):
ถ้า AI เพิ่งเสนอโปรแกรม **แค่ 1 รายการ** และลูกค้าพิมพ์ "สนใจ" / "สนใจครับ" / "สนใจค่ะ" / "โอเค" / "ได้เลย" / "เอาเลย"
→ ถือว่าลูกค้าสนใจโปรแกรมนั้นทันที ห้ามถามว่า "สนใจตัวไหนคะ"
→ ให้ยืนยันโปรแกรมนั้นและถามจำนวนผู้เดินทาง
→ ตัวอย่าง: "สนใจฮอกไกโด [ชื่อ] ใช่ไหมคะ 😊 ราคาเริ่ม X บาท ต้องการเดินทางกี่ท่านคะ?"

══════════════════════════════════
กฎงบประมาณ — สำคัญมาก
══════════════════════════════════
ถ้าลูกค้าบอกงบ เช่น "งบไม่เกิน 20,000" หรือ "ราคาถูกสุด":
- จำประเทศที่กำลังคุยอยู่ไว้เสมอ ห้ามถามประเทศใหม่ถ้าบทสนทนาก่อนหน้าระบุไว้แล้ว
- จากข้อมูลทัวร์ที่ได้มา (มี 12 โปรแกรม) คัดเฉพาะตัวที่ราคาไม่เกินงบก่อน
- ถ้าไม่มีในงบเลย บอกตัวที่ถูกที่สุดที่มี พร้อมบอกว่าเกินงบเท่าไหร่ และเสนอเพิ่มงบหรือเปลี่ยนประเทศ
- ห้ามแสดงทัวร์ที่แพงกว่างบมากโดยไม่อธิบาย
- ถ้าลูกค้าบอกว่า "บนเว็บมีราคา X" ตอบว่าจะให้ทีมงานเช็กราคานั้นโดยตรง ขอชื่อ+เบอร์ได้เลย

ถ้าลูกค้าพิมพ์ "เช็กเลย" หลังเลือกโปรแกรม → ถือเป็น hot lead
ให้สรุปข้อมูลโปรแกรมนั้นทันที แล้วถามเฉพาะ ชื่อผู้ติดต่อ + เบอร์/LINE เท่านั้น:

"ได้เลยค่ะ น้องแอดมินสรุปให้ทีมงานเช็กตัวนี้นะคะ 🔍

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

เมื่อมีข้อมูลจาก HTML detail / PDF → ตอบในรูปแบบนี้ทันที (ไม่ต้องรอให้ลูกค้าถามทีละข้อ):

✈️ *[ชื่อโปรแกรม]*
📌 รหัส: [code] | [จำนวนวัน] | [สายการบิน]
💰 ราคาเริ่ม: *[ราคา] บาท*
📅 วันเดินทาง: [รอบที่มี]

💳 ค่าวีซ่า: [รวมในราคา / ต้องทำเอง / ราคา / ไม่พบข้อมูล]
🤝 ค่าทิป: [X บาท/คน/ทริป / ไม่พบในเอกสาร]
🛏 พักเดี่ยวเพิ่ม: [X บาท / ไม่พบในเอกสาร]
💵 ค่ามัดจำ: [X บาท / ไม่พบในเอกสาร]

🔗 [ลิงก์โปรแกรม]

กฎสำคัญ:
- ต้องแสดงทุก field ข้างบน แม้บางตัวจะ "ไม่พบในเอกสาร"
- ห้ามเดาตัวเลขถ้าไม่พบใน data
- ถ้าถามแค่ข้อเดียวเช่น "ทิปเท่าไหร่" → ตอบตรงๆ แล้วแสดง detail ครบตาม format นี้เลย
- ห้ามถามกลับว่า "อยากทราบส่วนไหนเป็นพิเศษ"
- หลังแสดง detail → ถามเพียง 1 คำถาม โดยยึดตาม NEXT STEP ใน context:
  • ถ้าไม่รู้เดือน → "ช่วงเดือนไหนที่สะดวกเดินทางคะ?"
  • ถ้ารู้เดือนแล้ว แต่ยังไม่รู้วัน → "เดือน [X] มีรอบ [วัน1/วัน2/วัน3] ต้องการรอบไหนคะ?"
  • ถ้ารู้วันแล้ว แต่ยังไม่รู้จำนวนคน → "ต้องการเดินทางกี่ท่านคะ?"
  • ถ้ารู้วัน+จำนวนคนแล้ว → "ขอชื่อ+เบอร์ไว้ให้ทีมงานยืนยันที่นั่งด้วยค่ะ"
  • ถ้ารู้ชื่อ+เบอร์แล้ว → สรุปการจองและส่งทีมงาน

ถ้า data ว่างเปล่า (อ่านไม่ได้เลย) → ตอบว่า:
"ขอโทษนะคะ ระบบอ่านเอกสารโปรแกรมนี้ไม่ได้ในขณะนี้ ขอส่งให้ทีมงานเช็กและแจ้งรายละเอียดกลับให้ค่ะ ขอชื่อและเบอร์ติดต่อได้เลยนะคะ"

══════════════════════════════════
เมื่อลูกค้าขอดูโปรแกรมบนเว็บ
══════════════════════════════════
ถ้าลูกค้าถามว่า: "ดูโปรแกรมบนเว็บได้ไหม" / "หาในเว็บให้หน่อย" / "มีโปรอะไรบ้าง"
/ "มีทัวร์ไฟไหม้ไหม" / "ช่วยแนะนำจากเว็บ"

→ ห้ามตอบแค่ส่งลิงก์เว็บให้ลูกค้าไปหาเอง
→ ให้ trigger action=search หรือ flash_sale แล้วคัด 1-3 โปรแกรมจากเว็บมาตอบ
→ ถ้าข้อมูลยังไม่พอ ให้ถามเพียง 1 คำถามที่สำคัญที่สุด เช่น "สนใจเดินทางเดือนไหนคะ?"

══════════════════════════════════
ขั้นตอนการจองแบบ step-by-step (สำคัญมาก)
══════════════════════════════════
เมื่อลูกค้าสนใจโปรแกรมและมีข้อมูลครบ ให้ทำตามลำดับนี้ทีละขั้น:

【STEP 0】ถามเมือง/ประเทศ — แยกตามภูมิภาค

━━ กลุ่ม A: เอเชีย (ญี่ปุ่น จีน เกาหลี ไต้หวัน) ━━
ประเทศเดียวแต่มีหลายเส้นทาง → ต้องถามเมือง/เส้นทางก่อน search
  → ถ้าระบุเมืองมาแล้ว (เช่น "โอซาก้า" "ฮอกไกโด" "เฉิงตู" "เซี่ยงไฮ้") → ข้ามทันที ❌ ห้ามถามซ้ำ
  ตัวอย่าง:
    ลูกค้า: "สนใจทัวร์ญี่ปุ่น"
    ✅ "ได้ค่ะ มีหลายเส้นทางเลยนะคะ สนใจเมืองหรือเส้นทางไหนเป็นพิเศษคะ?
       เช่น โตเกียว • โอซาก้า • ฮอกไกโด • ฟูกูโอกะ หรือรูปแบบอื่นคะ? 😊"
    ลูกค้า: "สนใจทัวร์จีน"
    ✅ "ได้ค่ะ จีนมีหลายเส้นทางนะคะ ชอบแบบไหนคะ?
       เช่น เฉิงตู (หมีแพนด้า) • คุนหมิง • จางเจียเจี้ย • เซี่ยงไฮ้ • ปักกิ่ง 😊"

━━ กลุ่ม B: ยุโรป ━━
ประเทศ = ปลายทาง (ไม่ต้องถามเมืองเพิ่ม — ลูกค้าระบุประเทศก็พอ)
  → ลูกค้าบอก "ยุโรป" กว้างๆ ไม่ระบุประเทศ → ถามประเทศก่อน ❌ ห้าม search ทันที
    ✅ "ยุโรปมีหลายประเทศเลยนะคะ 😊 สนใจประเทศหรือเส้นทางไหนเป็นพิเศษคะ?
       🇮🇹 อิตาลี • 🇫🇷 ฝรั่งเศส • 🇨🇭 สวิตเซอร์แลนด์ • 🇬🇧 อังกฤษ
       🇬🇷 กรีซ • 🇹🇷 ตุรกี • 🇪🇸 สเปน • หรืออยากแพ็คเกจหลายประเทศคะ?"
  → ลูกค้าระบุประเทศในยุโรปแล้ว (เช่น อิตาลี ฝรั่งเศส สวิส) → search ได้เลย ❌ ห้ามถามเมืองเพิ่ม
  → ลูกค้าระบุเมืองในยุโรป (เช่น เวนิส ปารีส โรม) → ใช้เป็น city_hint สำหรับ filter เท่านั้น ❌ ห้ามถามซ้ำ
  ตัวอย่าง:
    "ทัวร์อิตาลี ฝรั่งเศส ที่มีไปเวนิสด้วย มีไหม"
    → search country_id สำหรับยุโรป, city_hint="เวนิส"
    ✅ "ได้ค่ะ จะช่วยหาโปรแกรมที่มีเวนิสให้นะคะ 😊"

━━ กลุ่ม C: ประเทศเดี่ยว (ฮ่องกง เวียดนาม สิงคโปร์ มาเลเซีย) ━━
→ ข้ามทันที → search ได้เลย ❌ ห้ามถามเมืองเพิ่ม

【STEP 1】ยืนยันเดือน/ช่วงที่สะดวกเดินทาง (ถ้ายังไม่รู้)
  → ถ้ารู้แล้ว → ข้ามไป STEP 2 ทันที ❌ ห้ามถามซ้ำ
  ตัวอย่าง: "ช่วงเดือนไหนที่สะดวกเดินทางคะ?"

【STEP 2】ให้ลูกค้าเลือกวันเดินทาง (ถ้ายังไม่รู้)
  → แสดงรอบที่มีในโปรแกรมตามเดือนที่ลูกค้าบอก
  ตัวอย่าง: "เดือน [X] มีรอบ: 5 มิ.ย. / 12 มิ.ย. / 19 มิ.ย. ค่ะ ต้องการรอบไหนคะ?"
  → ถ้ารู้แล้ว → ข้ามไป STEP 3 ทันที ❌ ห้ามถามซ้ำ

【STEP 3】ยืนยันจำนวนผู้เดินทาง (ถ้ายังไม่รู้)
  → ถ้ารู้แล้ว → ข้ามไป STEP 4 ทันที ❌ ห้ามถามซ้ำ
  ตัวอย่าง: "ขอถามเพิ่มเติมนะคะ ต้องการเดินทางกี่ท่านคะ?"

【STEP 4】ขอข้อมูลติดต่อ (ชื่อ + เบอร์/LINE)
  → เมื่อได้วันเดินทาง + จำนวนคนแล้ว:
  "ขอชื่อผู้ติดต่อและเบอร์โทร/LINE ไว้ให้ทีมงานยืนยันที่นั่งด้วยค่ะ"

【STEP 5】สรุปการจองครบถ้วน
  → เมื่อได้ชื่อ+เบอร์แล้ว สรุปทันที:
  ──────────────────
  📋 สรุปการจอง
  ──────────────────
  ✈️ โปรแกรม: [ชื่อโปรแกรม]
  📌 รหัส: [รหัส] | [จำนวนวัน]
  📅 วันเดินทาง: [วันที่เลือก]
  👥 จำนวน: [X] ท่าน
  💰 ราคาเริ่ม: [ราคา] บาท/ท่าน
  👤 ผู้ติดต่อ: [ชื่อ]
  📱 เบอร์/LINE: [เบอร์]
  ──────────────────
  แล้วแจ้ง: "ส่งข้อมูลให้ทีมงานแล้วค่ะ 😊 จะติดต่อกลับภายใน 15-30 นาที เพื่อยืนยันที่นั่งและแจ้งรายละเอียดการชำระเงินค่ะ"

หมายเหตุ: AI ไม่สามารถยืนยันที่นั่ง รับเงิน หรือยืนยันราคา final ได้ — ทีมงานจะดำเนินการ

══════════════════════════════════
เมื่อลูกค้าขอคุยกับคนจริง / เซลล์ / แอดมิน
══════════════════════════════════
ตอบทันที: "รับทราบค่ะ ส่งให้ทีมงานแล้ว จะติดต่อกลับเร็วๆ นี้ หรือทัก LINE @tourfiremai ได้เลยค่ะ 😊"
ห้ามถามข้อมูลเพิ่มหรือพยายามโน้มน้าวต่อ — จบบทสนทนาทันที

══════════════════════════════════
กฎห้ามพูดว่ากำลังดึงข้อมูล
══════════════════════════════════
❌ ห้ามพูดว่า "กำลังดึงรายละเอียด", "กำลังค้นหา", "รอสักครู่", "ให้แอดมินเช็ก"
✅ ส่งข้อมูลมาเลยโดยตรง ไม่ต้องบอกว่ากำลังทำอะไร

══════════════════════════════════
การแสดงข้อมูลทัวร์ (Search / แนะนำหลายโปรแกรม)
══════════════════════════════════
เมื่อมีข้อมูลทัวร์จากเว็บ ให้แสดงในรูปแบบนี้ต่อ 1 โปรแกรม:

✈️ *[ชื่อทัวร์]*
📌 รหัส: [code] | [จำนวนวัน] | [สายการบิน]
💰 ราคาเริ่ม: *[ราคา] บาท*
📅 วันเดินทาง: [วัน/เดือน]
🔗 [ลิงก์โปรแกรม]
💡 [เหตุผล 1 ประโยคว่าทำไมเหมาะ]

- เลือก 1-3 โปรแกรมที่เหมาะที่สุด
- ถ้าใกล้เดินทาง (≤45 วัน) → ใส่ 🔥 และระบุว่าเป็น "ทัวร์ไฟไหม้ โอกาสดี!"
- ถ้างบไม่พอ → แนะนำทางเลือกอื่นหรือประเทศใกล้เคียงได้เลย

══════════════════════════════════
การแสดงรายละเอียดโปรแกรม (Detail จาก PDF)
══════════════════════════════════
เมื่อลูกค้าเลือกโปรแกรมแล้วขอรายละเอียด → แสดงในรูปแบบนี้เสมอ:

✈️ *[ชื่อทัวร์]*
📌 รหัส: [code] | [จำนวนวัน] | [สายการบิน]
💰 ราคาเริ่ม: *[ราคา] บาท*
📅 วันเดินทาง: [รอบที่มี]

💳 ค่าวีซ่า: [รวมในราคา / ต้องทำเอง / ราคา]
🤝 ค่าทิป: [จำนวน บาท/คน/ทริป]
🛏 พักเดี่ยว: [ราคา บาท]

🔗 [ลิงก์โปรแกรม]

ถ้าข้อมูลบางรายการไม่พบใน PDF → ระบุ "ไม่พบในเอกสาร กรุณาสอบถามทีมงาน"
ห้ามถามลูกค้าว่า "อยากทราบส่วนไหนเป็นพิเศษ" — ส่งข้อมูลหลักมาเลย
หลังแสดง detail → ถามเพียง 1 คำถาม เช่น "สนใจเช็กที่นั่งได้เลยนะคะ ขอชื่อ+เบอร์ติดต่อด้วยค่ะ 😊"

เมื่อมีข้อมูลจาก HTML detail / PDF โปรแกรม:
- ตอบคำถามที่ลูกค้าถามโดยตรงก่อน แล้วแสดง detail ครบ (ทิป วีซ่า มัดจำ พักเดี่ยว) ทันทีเลย
- ไม่ต้องรอให้ลูกค้าถามทีละข้อ — ให้ข้อมูลครบในครั้งเดียว

กฎวันเดินทาง:
- ≤ 3 รอบ → แสดงทุกรอบ
- > 3 รอบ span ≤ 30 วัน → แสดงวันแรก-วันสุดท้าย
- > 3 รอบ span > 30 วัน → แสดงเดือน
- ข้ามรอบที่ผ่านมาแล้ว

══════════════════════════════════
เมื่อลูกค้าส่งสลิปการโอนเงิน
══════════════════════════════════
ถ้าลูกค้าส่งรูป (สลิป) หรือพูดว่า "โอนแล้ว" / "จ่ายแล้ว" / "ชำระแล้ว" / "ส่งสลิปแล้ว":
→ ตอบทันที: "ได้รับแล้วค่ะ ขอบคุณนะคะ 🙏 น้องแอดมินแจ้งทีมงานตรวจสอบและยืนยันการจองให้เลยค่ะ กรุณารอสักครู่นะคะ"

══════════════════════════════════
ใบนัดหมายการเดินทาง
══════════════════════════════════
ถ้าลูกค้าถาม "ใบนัดหมาย" / "เอกสารเดินทาง" / "ได้รับเอกสารแล้วยัง" / "ยังไม่ได้รับอะไรเลย":
→ ตอบ: "ทีมงานจะส่งใบนัดหมายการเดินทางให้ภายใน 2-5 วันก่อนวันเดินทางค่ะ 😊 ถ้าใกล้วันเดินทางแล้วยังไม่ได้รับ ทักแอดมินได้เลยนะคะ LINE @tourfiremai"

══════════════════════════════════
การขอรีวิวหลังเดินทาง
══════════════════════════════════
ถ้าลูกค้าบอกว่า "กลับมาแล้ว" / "ทริปดีมาก" / "เพิ่งกลับ" หรือแสดงสัญญาณว่าเดินทางเสร็จแล้ว:
→ ตอบ: "ยินดีด้วยนะคะที่กลับมาโดยสวัสดิภาพ 🎉 หวังว่าทริปจะสนุกมากเลยค่ะ ถ้าชอบก็ฝากรีวิวให้เพจด้วยนะคะ จะเป็นประโยชน์กับลูกค้าท่านอื่นมากเลยค่ะ 😊 https://www.facebook.com/tourfiremai/reviews"

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
"ตอนนี้ยังไม่เจอโอซาก้าที่ตรงงบ 40,000 พอดีค่ะ น้องแอดมินแนะนำได้ 2 ทาง:
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
def decide_action(user_message: str, history: list, last_options_count: int = 0) -> dict:
    history_text = ""
    for msg in history[-10:]:
        role = "ลูกค้า" if msg["role"] == "user" else "AI"
        history_text += f"{role}: {msg['content'][:250]}\n"

    last_opts_hint = f"\n⚠️ last_options_count={last_options_count} (จำนวนทัวร์ที่เสนอล่าสุดใน context)" if last_options_count > 0 else ""
    prompt = (
        f"บทสนทนาที่ผ่านมา:\n{history_text}\n"
        f"--- ข้อความล่าสุดของลูกค้า (สำคัญที่สุด): {user_message} ---\n"
        f"{last_opts_hint}\n\n"

        "ตอบเป็น JSON เท่านั้น (ห้ามมีข้อความอื่น):\n"
        "{\n"
        '  "action": "search" | "detail" | "detail_pdf" | "flash_sale" | "handoff" | "reply" | "continue",\n'
        '  "country_id": "เลขประเทศ หรือ null",\n'
        '  "country_name": "ชื่อประเทศภาษาไทย หรือ null",\n'
        '  "city": "ชื่อเมือง/จังหวัดที่ลูกค้าถามถึง เช่น โอซาก้า โตเกียว ฮอกไกโด เฉิงตู คุนหมิง หรือ null",\n'
        '  "month": "เดือนที่ลูกค้าระบุ เช่น มิ.ย. 69 หรือ null",\n'
        '  "budget_per_person": จำนวนเงินงบต่อคน (integer) หรือ null,\n'
        '  "pax": จำนวนคน (integer) หรือ null,\n'
        '  "selected_option_index": 1 | 2 | 3 | null,\n'
        '  "uses_previous_option": true | false,\n'
        '  "clear_previous_options": true | false,\n'
        '  "lead_stage": "cold" | "warm" | "hot" | "booking",\n'
        '  "reason": "เหตุผลสั้นๆ ที่เลือก action นี้"\n'
        "}\n\n"

        "=== กฎ action (เรียงตามความสำคัญ) ===\n\n"

        "⚠️ กฎ SINGLE-OPTION — ตรวจสอบก่อนทุกกฎอื่น:\n"
        "ถ้า last_options_count == 1 AND ข้อความล่าสุดมีคำว่า สนใจ/สนใจครับ/สนใจค่ะ/เอาตัวนี้/เช็กที่นั่ง/ขอรายละเอียด/จองเลย/โอเค/ได้เลย\n"
        "→ action=detail, uses_previous_option=true, selected_option_index=1, lead_stage=hot\n"
        "→ ห้ามถามว่าสนใจตัวไหน เพราะมีแค่ตัวเดียว\n\n"
        "ถ้า last_options_count > 1 AND ข้อความล่าสุดพูดว่า สนใจ (ไม่ระบุเลข)\n"
        "→ action=reply (ให้ถามว่าสนใจตัวที่เท่าไหร่)\n\n"
        "⚠️ กฎ CONTINUATION — ตรวจสอบก่อนทุกกฎอื่น:\n"
        "ถ้าข้อความล่าสุดเป็น คำสั้นๆ เช่น 'ไหน', 'ไหนครับ', 'ไหนคะ', 'ได้ยัง', 'รออยู่', 'ส่งมา', 'มีไหม', 'หาได้ไหม', 'ดูให้หน่อย', 'แล้วไง', 'ยังไง'\n"
        "AND ใน history ก่อนหน้า AI เคยบอกว่าจะค้นหา/เช็ก/ดึงข้อมูล/รอสักครู่\n"
        "→ action=continue, country_id=ประเทศล่าสุดจาก history, lead_stage ตาม context\n\n"

        "action=search: ลูกค้าต้องการดูโปรแกรมทัวร์ประเทศที่ระบุ รวมถึงการเปลี่ยนประเทศ\n"
        "action=detail: ลูกค้าขอดูรายละเอียดทัวร์ — ใช้เมื่อยังไม่มีโปรแกรมที่เลือกใน context\n"
        "action=detail_pdf: (1) ลูกค้าขอ 'รายละเอียด' ของโปรแกรมที่เลือกไว้ใน context แล้ว (2) ลูกค้าถามมัดจำ/วีซ่า/ทิป/พักเดี่ยว/เงื่อนไขยกเลิก/itinerary/โรงแรม/รวมอะไร — และมีโปรแกรมที่เลือกไว้ใน context\n"
        "action=flash_sale: ลูกค้าถามทัวร์ไฟไหม้/โปรโมชั่นพิเศษ/ดีลร้อน\n"
        "action=handoff: ลูกค้าพร้อมจอง/สนใจจอง/ขอคุยเซลล์/เช็กที่นั่ง/ขอราคา final/ขอส่วนลด/ยกเลิก\n"
        "action=reply: ทักทาย/ถามทั่วไป/ยังไม่ระบุประเทศ/ยุโรปรวม — ใช้เฉพาะตอนเริ่มบทสนทนาใหม่จริงๆ เท่านั้น\n"
        "  ⚠️ กรณียุโรป: ถ้าลูกค้าพูดว่า 'ยุโรป' โดยไม่ระบุประเทศ → action=reply, country_id=null\n"
        "  ให้ bot ถามประเทศที่สนใจ (อิตาลี/ฝรั่งเศส/สวิต/กรีซ/ตุรกี/อังกฤษ/สเปน)\n"
        "  ถ้าลูกค้าระบุประเทศยุโรปชัดเจน เช่น 'อิตาลี' → action=search, country_id=102\n\n"

        "=== กฎ clear_previous_options ===\n"
        "clear_previous_options=true เมื่อ: ลูกค้าเปลี่ยนประเทศ/เมืองใหม่ ทำให้ last_options ชุดก่อนใช้ไม่ได้แล้ว\n"
        "เช่น: 'เปลี่ยนเป็นเกาหลีแทน', 'ขอดูญี่ปุ่นแทน', 'ลองดูจีนดีกว่า'\n"
        "clear_previous_options=false: ลูกค้ายังสนใจชุดเดิม หรือเป็นคำถามทั่วไป\n\n"

        "=== กฎ selected_option_index และ uses_previous_option ===\n"
        "ถ้าลูกค้าพิมพ์ 'ตัวที่ 1/2/3', 'อันนี้', 'ตัวนี้', 'สนใจอันนี้' → uses_previous_option=true, selected_option_index=เลขที่ระบุ (เลขหมายถึงลำดับจากชุดโปรแกรมที่เสนอล่าสุดใน history)\n"
        "ถ้าลูกค้าพิมพ์ 'เช็กเลย' หลังมีโปรแกรมใน context → action=handoff, uses_previous_option=true, lead_stage=hot\n"
        "ถ้าลูกค้าเปลี่ยนประเทศจริง → uses_previous_option=false, country_id ใหม่\n\n"

        "=== กฎ lead_stage ===\n"
        "cold: เพิ่งทักมา ยังไม่รู้จะไปไหน\n"
        "warm: รู้ประเทศ/ช่วงเวลา เริ่มดูโปรแกรม\n"
        "hot: เลือกโปรแกรมแล้ว ถามรายละเอียด/มัดจำ/วีซ่า\n"
        "booking: บอกจอง/เช็กเลย/ขอติดต่อกลับ\n\n"

        "=== Country IDs ===\n"
        "เอเชีย: ญี่ปุ่น=2, เกาหลี=1, เวียดนาม=7, จีน=5, ฮ่องกง=3, สิงคโปร์=4, มาเลเซีย=6, ไต้หวัน=19, พม่า=8, ลาว=9, อินโดนีเซีย=18, มาเก๊า=29, ฟิลิปปินส์=104\n"
        "ยุโรป (ต้องระบุประเทศ — ไม่มี ID รวม): อิตาลี=102, สวิตฯ=64, สแกนดิ=47, อังกฤษ=42, เยอรมนี=100, ตุรเคีย=71, ออสเตรีย=159, สเปน=105, ฝรั่งเศส=101, กรีซ=169, โปรตุเกส=200, ยุโรปตะวันออก=80\n"
        "⚠️ 'ยุโรป' กว้างๆ → country_id=null, action=reply (รอถามประเทศก่อน)\n"
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
            city = data.get("city", "")
            data["city"] = city if city and city != "null" else None
            cname = data.get("country_name", "")
            data["country_name"] = cname if cname and cname != "null" else None
            month = data.get("month", "")
            data["month"] = month if month and month != "null" else None
            bgt = data.get("budget_per_person")
            data["budget_per_person"] = int(bgt) if bgt and str(bgt).isdigit() else None
            pax = data.get("pax")
            data["pax"] = int(pax) if pax and str(pax).isdigit() else None
            data["selected_option_index"] = data.get("selected_option_index")
            data["uses_previous_option"] = bool(data.get("uses_previous_option", False))
            data["clear_previous_options"] = bool(data.get("clear_previous_options", False))
            data["lead_stage"] = data.get("lead_stage", "cold")
            data["reason"] = data.get("reason", "")
            return data
    except Exception as e:
        logger.error(f"decide_action error: {e}")

    return {"action": "reply", "country_id": None, "country_name": None, "city": None,
            "month": None, "budget_per_person": None, "pax": None,
            "selected_option_index": None, "uses_previous_option": False,
            "clear_previous_options": False, "lead_stage": "cold", "reason": ""}


# ─── AI — Call 2: Generate Response ──────────────────────────────────────────
def generate_response(user_message: str, history: list, tour_data: str = "",
                      is_handoff: bool = False, ctx: dict = None) -> str:
    messages = []
    for msg in history[-10:]:
        messages.append({"role": msg["role"], "content": msg["content"]})

    if tour_data:
        user_content = (
            f"{user_message}\n\n"
            "--- ข้อมูลทัวร์จากเว็บ tourfiremai.com (ดึงตอนนี้) ---\n"
            f"{tour_data[:4000]}\n"
            "---\n"
            "ใช้ข้อมูลทัวร์ด้านบนในการตอบ คัดเลือก 1-3 โปรแกรมที่เหมาะที่สุดกับความต้องการลูกค้า "
            "พร้อมเหตุผล 1 ประโยคต่อตัวเลือก"
            + (f"\n[งบลูกค้า {ctx['budget_per_person']} บาท: คัดเฉพาะที่ราคาไม่เกินงบก่อน ถ้าไม่มีให้โชว์ถูกสุดและบอกว่าเกินเท่าไหร่]" if ctx and ctx.get("budget_per_person") else "")
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
            system=_system_prompt(ctx=ctx),
            messages=messages
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.error(f"generate_response error: {e}")
        return "ขออภัยค่ะ ระบบมีปัญหาชั่วคราว กรุณาลองใหม่อีกครั้ง หรือติดต่อแอดมินได้เลยนะคะ 😊"


# ─── Payment slip handler ─────────────────────────────────────────────────────
_PAYMENT_KEYWORDS = [
    "โอนแล้ว", "จ่ายแล้ว", "ชำระแล้ว", "ส่งสลิป", "สลิปแล้ว",
    "โอนเงินแล้ว", "ชำระเงินแล้ว", "จ่ายเงินแล้ว", "มัดจำแล้ว",
    "โอนมัดจำแล้ว", "โอนค่าทัวร์แล้ว", "ส่งหลักฐาน",
]

def process_payment_slip(sender_id: str, image_urls: list = None):
    """เรียกเมื่อลูกค้าส่งสลิปการโอนเงิน (รูปภาพ หรือ text keyword)"""
    ctx = get_context(sender_id)
    ctx["payment_received"] = True
    ctx["lead_stage"] = "paid"
    save_context(sender_id, ctx)

    summary_parts = [f"💳 สลิปการโอน!\nPSID: {sender_id}"]
    if ctx.get("customer_name"):
        summary_parts.append(f"ชื่อ: {ctx['customer_name']}")
    if ctx.get("phone"):
        summary_parts.append(f"เบอร์/LINE: {ctx['phone']}")
    if ctx.get("selected_tour_name"):
        summary_parts.append(f"โปรแกรม: {ctx['selected_tour_name']}")
    elif ctx.get("last_options"):
        opts = ctx["last_options"]
        if isinstance(opts, str):
            try: opts = json.loads(opts)
            except: opts = []
        if opts:
            summary_parts.append(f"โปรแกรม: {opts[0].get('name', '')}")
    if ctx.get("travel_date"):
        summary_parts.append(f"วันเดินทาง: {ctx['travel_date']}")
    if ctx.get("pax"):
        summary_parts.append(f"จำนวน: {ctx['pax']} ท่าน")
    if image_urls:
        summary_parts.append(f"รูปสลิป: {image_urls[0]}")
    summary_parts.append("→ กรุณาตรวจสอบและยืนยันการจองด่วน!")
    notify_line("\n".join(summary_parts))

    save_lead_supabase(sender_id, ctx, "paid", "ส่งสลิปการโอนเงิน")

    reply = (
        "ได้รับสลิปแล้วค่ะ ขอบคุณมากนะคะ 🙏 "
        "น้องแอดมินแจ้งทีมงานตรวจสอบและยืนยันการจองให้เลยค่ะ "
        "กรุณารอสักครู่ ทีมงานจะติดต่อยืนยันกลับหาคุณอีกครั้งนะคะ 😊"
    )
    send_message(sender_id, reply)
    save_to_history(sender_id, "assistant", reply)
    logger.info(f"✅ Payment slip processed for {sender_id}")


# ─── Core message processing ──────────────────────────────────────────────────
def process_message(sender_id: str, text: str):
    """Main logic — รันใน background thread"""
    logger.info(f"Processing [{sender_id}]: {text[:80]}")
    try:
        history = list(get_history(sender_id))
        ctx = get_context(sender_id)

        # ── Payment slip detection via text keywords ──────────────────────────
        text_lower = text.lower()
        if any(kw in text_lower for kw in _PAYMENT_KEYWORDS):
            logger.info(f"💳 Payment keyword detected for {sender_id}")
            save_to_history(sender_id, "user", text)
            process_payment_slip(sender_id)
            return

        _last_opts_count = len(ctx.get("last_options", []))
        action_data = decide_action(text, history, last_options_count=_last_opts_count)
        action               = action_data.get("action", "reply")
        country_id           = action_data.get("country_id")
        selected_option_idx  = action_data.get("selected_option_index")
        uses_previous        = action_data.get("uses_previous_option", False)
        clear_prev_options   = action_data.get("clear_previous_options", False)
        lead_stage           = action_data.get("lead_stage", "cold")
        city_hint            = action_data.get("city") or action_data.get("city_hint")
        classifier_month     = action_data.get("month")
        classifier_budget    = action_data.get("budget_per_person")
        classifier_pax       = action_data.get("pax")
        classifier_country   = action_data.get("country_name")
        logger.info(f"Action: {action}, country_id: {country_id}, city: {city_hint}, lead_stage: {lead_stage}, "
                    f"selected_idx: {selected_option_idx}, uses_prev: {uses_previous}, clear_prev: {clear_prev_options}")

        # ── Apply classifier fast-fills to context ────────────────────────
        ctx["last_user_message"] = text[:500]
        if classifier_month and not ctx.get("month"):
            ctx["month"] = classifier_month
        if classifier_budget and not ctx.get("budget_per_person"):
            ctx["budget_per_person"] = classifier_budget
        if classifier_pax and not ctx.get("pax"):
            ctx["pax"] = classifier_pax
        if classifier_country and not ctx.get("country"):
            ctx["country"] = classifier_country
        if country_id and not ctx.get("country_id"):
            ctx["country_id"] = country_id

        # ── Resolve selected_option_index → set selected_tour ────────────
        if uses_previous and selected_option_idx and ctx.get("last_options"):
            opts = ctx["last_options"]
            if isinstance(opts, str):
                try:
                    opts = json.loads(opts)
                except Exception:
                    opts = []
            if isinstance(opts, list) and 1 <= (selected_option_idx or 0) <= len(opts):
                selected = opts[selected_option_idx - 1]
                ctx["selected_tour"]      = selected
                ctx["selected_tour_name"] = selected.get("name", "")
                ctx["selected_tour_url"]  = selected.get("url", selected.get("link", ""))
                logger.info(f"✅ Resolved option #{selected_option_idx}: {ctx['selected_tour_name']}")

        # ── clear_previous_options: ลูกค้าเปลี่ยนประเทศ ──────────────────
        if clear_prev_options:
            ctx["last_options"] = []
            ctx["selected_tour"] = None
            ctx["selected_tour_name"] = None
            ctx["selected_tour_url"] = None
            ctx["city_hint"] = None
            logger.info(f"🔄 clear_previous_options triggered for {sender_id}")
        save_context(sender_id, ctx)

        tour_data   = ""
        is_handoff  = False

        # action=continue → ใช้ country_id ล่าสุด
        if action == "continue":
            if not country_id:
                # ลอง context ก่อน
                if ctx.get("country"):
                    cname = ctx["country"]
                    for cid, cname2 in COUNTRY_MAP.items():
                        if cname2 == cname:
                            country_id = cid
                            break
                # fallback: ค้นใน history
                if not country_id:
                    for msg in reversed(history):
                        c = re.search(r'country_id["\s:]+(\d+)', msg.get("content", ""))
                        if c:
                            country_id = c.group(1)
                            break
            action = "search"
            logger.info(f"Continuation detected → search country_id={country_id}")

        # Fetch tour data if needed
        if action in ("search", "detail") and country_id:
            country_name = COUNTRY_MAP.get(country_id, country_id)
            logger.info(f"Fetching tours: {country_name} (id={country_id})")
            try:
                budget_max = None
                if ctx and ctx.get("budget_per_person"):
                    try:
                        b = str(ctx["budget_per_person"]).replace(",","").replace(" ","")
                        budget_max = int(b)
                    except Exception:
                        pass
                result = fetch_tours(country_id, city_hint=city_hint, budget_max=budget_max)
                if isinstance(result, tuple):
                    tour_data, tour_meta = result
                else:
                    tour_data, tour_meta = result, []
                # อัพเดท last_options ใน Redis ทันที — ไม่รอ background thread
                if tour_meta:
                    ctx["last_options"] = tour_meta
                    if city_hint:
                        ctx["city_hint"] = city_hint
                    # ── Auto-select when only 1 result returned ───────────
                    if len(tour_meta) == 1:
                        t0 = tour_meta[0]
                        ctx["selected_tour"] = t0
                        ctx["selected_tour_name"] = t0.get("name", "")
                        ctx["selected_tour_url"]  = t0.get("url", t0.get("link", ""))
                        logger.info(f"🎯 Auto-selected single tour: {ctx['selected_tour_name']}")
                    save_context(sender_id, ctx)
                    logger.info(f"last_options updated immediately: {len(tour_meta)} tours")
            except Exception as e:
                logger.error(f"fetch_tours error: {e}")
                tour_data, tour_meta = "", []

        # Fetch PDF info
        if action == "detail_pdf":
            program_url = extract_program_url_from_history(history)
            if program_url:
                logger.info(f"Fetching full detail for: {program_url}")
                try:
                    # Step 1: HTML detail page (เร็ว มีทิป/มัดจำ/วีซ่าในเนื้อหา)
                    html_detail = fetch_tour_detail_full(program_url)
                    # Step 2: PDF (itinerary, เงื่อนไขละเอียด)
                    pdf_detail = fetch_pdf_info(program_url)
                    # รวมกัน — HTML ก่อน PDF
                    parts = []
                    if html_detail:
                        parts.append(html_detail)
                    if pdf_detail:
                        parts.append(pdf_detail)
                    tour_data = "\n\n".join(parts) if parts else ""
                except Exception as e:
                    logger.error(f"fetch detail error: {e}")
                    tour_data = ""
            else:
                action = "reply"

        # Notify admin
        if action == "flash_sale":
            notify_line(f"🔥 ทัวร์ไฟไหม้!\nPSID: {sender_id}\nข้อความ: {text}")
        elif action == "handoff":
            is_handoff = True
            stage_emoji = {"hot": "🔔", "booking": "📋", "warm": "💬", "paid": "💳"}.get(lead_stage, "📩")
            ctx_summary = ""
            if ctx.get("customer_name"):
                ctx_summary += f"\nชื่อ: {ctx['customer_name']}"
            if ctx.get("phone"):
                ctx_summary += f"\nเบอร์/LINE: {ctx['phone']}"
            if ctx.get("selected_tour_name"):
                ctx_summary += f"\nโปรแกรม: {ctx['selected_tour_name']}"
            elif ctx.get("destination"):
                ctx_summary += f"\nปลายทาง: {ctx['destination']}"
            if ctx.get("travel_date"):
                ctx_summary += f"\nวันเดินทาง: {ctx['travel_date']}"
            if ctx.get("pax"):
                ctx_summary += f"\nจำนวน: {ctx['pax']} ท่าน"
            if ctx.get("budget_per_person"):
                ctx_summary += f"\nงบ: {ctx['budget_per_person']:,}" if isinstance(ctx['budget_per_person'], (int, float)) else f"\nงบ: {ctx['budget_per_person']}"
            notify_line(
                f"{stage_emoji} Lead [{lead_stage.upper()}]\nPSID: {sender_id}\nข้อความ: {text}{ctx_summary}"
            )

        # Save user message
        save_to_history(sender_id, "user", text)

        # Generate response (with context injected into system prompt)
        reply = generate_response(text, history, tour_data, is_handoff, ctx=ctx)

        # Save AI reply
        save_to_history(sender_id, "assistant", reply)

        # Track pending_action — what AI just said it will do
        if action in ("search", "detail", "detail_pdf"):
            ctx["pending_action"] = f"{action}_{country_id or 'unknown'}"
        elif action == "handoff":
            ctx["pending_action"] = "handoff_sent"
        elif action == "flash_sale":
            ctx["pending_action"] = "flash_sale_notified"
        else:
            ctx["pending_action"] = None
        ctx["last_bot_message"] = reply[:600]
        save_context(sender_id, ctx)

        send_message(sender_id, reply)

        # ── Background: extract context + save lead ──────────────────────
        def _update_crm():
            try:
                updated_history = get_history(sender_id)
                new_ctx = extract_context_after_response(sender_id, updated_history, reply)
                save_context(sender_id, new_ctx)
                logger.info(f"Context updated for {sender_id}: dest={new_ctx.get('destination')}, stage={lead_stage}")

                # Upsert customer record whenever we have name or phone
                if new_ctx.get("customer_name") or new_ctx.get("phone"):
                    save_customers_supabase(sender_id, new_ctx)

                # Save to Supabase for hot/booking leads, or any handoff
                if lead_stage in ("hot", "booking", "paid", "awaiting_docs", "complete") or action == "handoff":
                    save_lead_supabase(sender_id, new_ctx, lead_stage, text)
                elif lead_stage == "warm" and new_ctx.get("destination"):
                    # warm leads with destination also worth tracking
                    save_lead_supabase(sender_id, new_ctx, lead_stage, text)
            except Exception as e:
                logger.error(f"_update_crm error: {e}", exc_info=True)

        crm_thread = threading.Thread(target=_update_crm)
        crm_thread.daemon = True
        crm_thread.start()

    except Exception as e:
        logger.error(f"process_message error: {e}", exc_info=True)
        try:
            send_message(
                sender_id,
                "ขออภัยค่ะ ระบบมีปัญหาชั่วคราว กรุณาลองใหม่ หรือทักแอดมินได้เลยนะคะ 🙏"
            )
        except Exception:
            pass


# ─── Dashboard ────────────────────────────────────────────────────────────────
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>รวมทัวร์ไฟไหม้ — Lead Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', Tahoma, sans-serif; background: #f0f4f8; color: #333; }
  header { background: linear-gradient(135deg, #e63946, #ff6b35); color: #fff; padding: 18px 28px; }
  header h1 { font-size: 1.4rem; }
  header p { font-size: 0.85rem; opacity: 0.85; margin-top: 4px; }
  .stats { display: flex; gap: 14px; padding: 20px 28px; flex-wrap: wrap; }
  .stat-card { background: #fff; border-radius: 12px; padding: 18px 22px; flex: 1; min-width: 130px;
               box-shadow: 0 2px 8px rgba(0,0,0,0.07); text-align: center; }
  .stat-card .num { font-size: 2rem; font-weight: 700; }
  .stat-card .label { font-size: 0.8rem; color: #666; margin-top: 4px; }
  .cold .num { color: #aaa; }
  .warm .num { color: #f59e0b; }
  .hot .num { color: #ef4444; }
  .booking .num { color: #10b981; }
  .section { padding: 0 28px 28px; }
  .section h2 { font-size: 1rem; margin-bottom: 14px; color: #444; border-left: 4px solid #e63946;
                padding-left: 10px; }
  table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 12px;
          overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
  th { background: #fef2f2; color: #666; font-size: 0.78rem; text-transform: uppercase;
       padding: 10px 14px; text-align: left; }
  td { padding: 10px 14px; font-size: 0.88rem; border-top: 1px solid #f3f4f6; }
  tr:hover td { background: #fef9f9; }
  .badge { display: inline-block; border-radius: 6px; padding: 2px 8px; font-size: 0.75rem;
           font-weight: 600; }
  .badge-cold { background: #f3f4f6; color: #9ca3af; }
  .badge-warm { background: #fef3c7; color: #d97706; }
  .badge-hot { background: #fee2e2; color: #dc2626; }
  .badge-booking { background: #d1fae5; color: #059669; }
  .no-data { text-align: center; color: #aaa; padding: 40px; font-size: 0.9rem; }
  .refresh { float: right; background: #e63946; color: #fff; border: none; border-radius: 8px;
             padding: 6px 14px; cursor: pointer; font-size: 0.82rem; margin-top: -2px; }
  .refresh:hover { background: #c1121f; }
  .options-list { font-size: 0.78rem; color: #555; }
  .updated { font-size: 0.72rem; color: #aaa; }
  @media (max-width: 600px) {
    .stats { flex-direction: column; }
    table { font-size: 0.8rem; }
    td, th { padding: 8px 10px; }
  }
</style>
</head>
<body>
<header>
  <h1>🔥 รวมทัวร์ไฟไหม้ — Lead Dashboard</h1>
  <p>ข้อมูล Lead จาก AI Sales Bot (Messenger)</p>
</header>

<div class="stats">
  <div class="stat-card booking">
    <div class="num">{{ counts.get('booking', 0) }}</div>
    <div class="label">📋 Booking</div>
  </div>
  <div class="stat-card hot">
    <div class="num">{{ counts.get('hot', 0) }}</div>
    <div class="label">🔔 Hot</div>
  </div>
  <div class="stat-card warm">
    <div class="num">{{ counts.get('warm', 0) }}</div>
    <div class="label">💬 Warm</div>
  </div>
  <div class="stat-card cold">
    <div class="num">{{ counts.get('cold', 0) }}</div>
    <div class="label">❄️ Cold</div>
  </div>
</div>

<div class="section">
  <h2>📋 Booking + 🔔 Hot Leads
    <button class="refresh" onclick="location.reload()">🔄 รีเฟรช</button>
  </h2>
  {% if hot_leads %}
  <table>
    <thead>
      <tr>
        <th>Stage</th>
        <th>ชื่อ</th>
        <th>เบอร์/LINE</th>
        <th>ปลายทาง</th>
        <th>เดือน</th>
        <th>งบ/คน</th>
        <th>จำนวน</th>
        <th>โปรแกรมที่สนใจ</th>
        <th>ข้อความล่าสุด</th>
        <th>อัปเดต</th>
      </tr>
    </thead>
    <tbody>
    {% for lead in hot_leads %}
      <tr>
        <td><span class="badge badge-{{ lead.lead_stage }}">{{ lead.lead_stage }}</span></td>
        <td>{{ lead.customer_name or '—' }}</td>
        <td>{{ lead.phone or '—' }}</td>
        <td>{{ lead.destination or '—' }}</td>
        <td>{{ lead.month or '—' }}</td>
        <td>{{ '{:,}'.format(lead.budget_per_person) if lead.budget_per_person else '—' }}</td>
        <td>{{ lead.pax or '—' }}</td>
        <td class="options-list">
          {% set opts = lead.last_options %}
          {% if opts %}
            {% if opts is string %}{% set opts = opts | from_json %}{% endif %}
            {% for o in opts[:2] %}
              <div>{{ o.get('index','') }}. {{ o.get('name','')[:30] }}</div>
            {% endfor %}
          {% else %}—{% endif %}
        </td>
        <td>{{ (lead.last_message or '')[:60] }}{% if lead.last_message and lead.last_message|length > 60 %}…{% endif %}</td>
        <td class="updated">{{ lead.updated_at[:16].replace('T',' ') if lead.updated_at else '—' }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
  <div class="no-data">ยังไม่มี Hot/Booking Leads</div>
  {% endif %}
</div>

<div class="section">
  <h2>💬 Warm Leads ล่าสุด</h2>
  {% if warm_leads %}
  <table>
    <thead>
      <tr>
        <th>ชื่อ</th>
        <th>เบอร์/LINE</th>
        <th>ปลายทาง</th>
        <th>ประเทศ</th>
        <th>เดือน</th>
        <th>งบ/คน</th>
        <th>ข้อความล่าสุด</th>
        <th>อัปเดต</th>
      </tr>
    </thead>
    <tbody>
    {% for lead in warm_leads %}
      <tr>
        <td>{{ lead.customer_name or '—' }}</td>
        <td>{{ lead.phone or '—' }}</td>
        <td>{{ lead.destination or '—' }}</td>
        <td>{{ lead.country or '—' }}</td>
        <td>{{ lead.month or '—' }}</td>
        <td>{{ '{:,}'.format(lead.budget_per_person) if lead.budget_per_person else '—' }}</td>
        <td>{{ (lead.last_message or '')[:60] }}{% if lead.last_message and lead.last_message|length > 60 %}…{% endif %}</td>
        <td class="updated">{{ lead.updated_at[:16].replace('T',' ') if lead.updated_at else '—' }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
  <div class="no-data">ยังไม่มี Warm Leads</div>
  {% endif %}
</div>

<div style="text-align:center; padding: 20px; color: #bbb; font-size: 0.8rem;">
  รวมทัวร์ไฟไหม้ AI Sales Bot v3 • <a href="/health" style="color:#bbb">health check</a>
</div>
</body>
</html>
"""

@app.route("/dashboard", methods=["GET"])
def dashboard():
    # Simple password check via query param
    pw = request.args.get("pass", "")
    if pw != DASHBOARD_PASS:
        return (
            '<html><body style="font-family:sans-serif;text-align:center;padding:60px">'
            '<h2>🔒 Dashboard</h2>'
            '<p>ใส่ password: <code>/dashboard?pass=YOUR_PASSWORD</code></p>'
            '</body></html>'
        ), 401

    counts = count_leads_by_stage()
    hot_leads = list_leads_supabase(stage_filter=None, limit=100)
    # Filter client-side
    booking_hot = [l for l in hot_leads if l.get("lead_stage") in ("booking", "hot")]
    warm = [l for l in hot_leads if l.get("lead_stage") == "warm"][:20]

    # Jinja2 doesn't have from_json filter by default — parse last_options here
    for lead in booking_hot + warm:
        opts = lead.get("last_options")
        if opts and isinstance(opts, str):
            try:
                lead["last_options"] = json.loads(opts)
            except Exception:
                lead["last_options"] = []

    return render_template_string(
        DASHBOARD_HTML,
        counts=counts,
        hot_leads=booking_hot,
        warm_leads=warm,
    )


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
            if msg_event.get("message", {}).ge