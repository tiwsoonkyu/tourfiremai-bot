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
LINE_ADMIN_ID      = os.environ.get("LINE_ADMIN_ID", "")          # LINE User ID ของแอดมิน (personal)
LINE_GROUP_ID      = os.environ.get("LINE_GROUP_ID", "")          # LINE Group ID สำหรับแจ้งเตือนทีม
SUPABASE_URL      = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY      = os.environ.get("SUPABASE_KEY", "")
DASHBOARD_PASS    = os.environ.get("DASHBOARD_PASSWORD", "tourfiremai2024")

# ─── Go-Live Guard ────────────────────────────────────────────────────────────
# Set BOT_GO_LIVE_STARTED_AT=2026-05-10T15:00:00+07:00 in Railway when going live
# Set BOT_NEW_CONTEXT_ONLY=true to block legacy conversations
BOT_GO_LIVE_STARTED_AT_STR = os.environ.get("BOT_GO_LIVE_STARTED_AT", "")
BOT_NEW_CONTEXT_ONLY       = os.environ.get("BOT_NEW_CONTEXT_ONLY", "false").lower() == "true"

def _go_live_dt() -> datetime | None:
    """Parse BOT_GO_LIVE_STARTED_AT → UTC datetime (or None if unset)"""
    if not BOT_GO_LIVE_STARTED_AT_STR:
        return None
    try:
        s = BOT_GO_LIVE_STARTED_AT_STR
        if s.endswith("+07:00"):
            s = s[:-6]
            return datetime.fromisoformat(s) - timedelta(hours=7)
        if s.endswith("Z"):
            return datetime.fromisoformat(s.rstrip("Z"))
        return datetime.fromisoformat(s)
    except Exception as e:
        logger.warning(f"_go_live_dt parse error: {e}")
        return None

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
    "selected_tour_code": None,      # tour_code_real เช่น ZGNRT-2618VZ
    "selected_tour_web_code": None,  # web_code เช่น ap241533
    "selected_tour_airline": None,   # airline เช่น VZ
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
    # ── Search Mode ──────────────────────────────────────────────────────
    "search_mode": "normal",  # "normal"|"faimai"|"any"
    "deal_type": None,        # "normal"|"faimai"|None
    # ── Meta ──────────────────────────────────────────────────────────────
    "last_user_message": None,
    "last_bot_message": None,
    "pending_action": None,
    "updated_at": None,
    # ── Bot Session / Go-Live Guard ────────────────────────────────────────
    "bot_session_started_at": None,  # ISO — เวลาที่ bot เริ่ม session (หลัง go-live)
    "bot_memory_started_at":  None,  # ISO — เวลาที่ bot เริ่มจำ (= session start)
    "bot_allowed":            None,  # True = bot ตอบได้ | False = legacy blocked
    "legacy_conversation":    False, # True = มีประวัติก่อน go-live → block bot
    # ── Human Takeover / Pause ────────────────────────────────────────────
    "human_takeover":    False,
    "bot_paused_until":  None,       # ISO — หยุด bot จนถึงเวลานี้
    "bot_pause_reason":  None,       # image_handoff|payment|handoff|booking|legacy_conversation
    # ── Case ID ──────────────────────────────────────────────────────────
    "case_id":     None,  # TF-HHMM-XXXX
    "case_status": None,  # waiting_team | resolved
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
        f"โปรแกรมที่เลือก: {existing.get('selected_tour_name') or 'ยังไม่เลือก'}"
        + (f" [รหัส: {existing['selected_tour_code']}]" if existing.get('selected_tour_code') else "")
        + "\n\n"
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
        logger.info(f"decide_action raw: {raw[:200]}")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
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
            "customer_name":    "customer_name",
            "phone":            "phone",
            "destination":      "destination",
            "country":          "country",
            "month":            "month",
            "budget_per_person":"budget_per_person",
            "pax":              "pax",
            "travel_date":      "travel_date",
            "selected_tour_name":"selected_tour_name",
            # Dashboard fields
            "selected_tour_code":     "selected_tour_code_real",
            "selected_tour_web_code": "selected_web_code",
            "selected_tour_airline":  "selected_tour_airline",
            "ad_id":            "ad_id",
            "ad_ref":           "ad_ref",
            "ad_title":         "ad_title",
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

        # Last bot message
        lbm = context.get("last_bot_message", "")
        if lbm:
            payload["last_bot_message"] = lbm[:600]

        # Dashboard operational fields (pass as kwargs via context extras)
        for fld in ("needs_review", "review_reason", "status", "handoff_requested", "handoff_at", "channel"):
            v = context.get(f"_lead_{fld}")
            if v is not None:
                payload[fld] = v

        resp = requests.post(url, json=payload, headers=_sb_headers(prefer_upsert=True), timeout=10)
        if resp.ok or resp.status_code == 201:
            logger.info(f"✅ Lead upserted: {psid} [{lead_stage}]")
        else:
            logger.error(f"❌ Supabase {resp.status_code}: {resp.text[:300]}")
    except Exception as e:
        logger.error(f"save_lead_supabase error: {e}")

def log_chat_event(
    psid: str,
    event_type: str,
    ctx: dict = None,
    message: str = "",
    bot_reply: str = "",
    intent: str = "",
    needs_review: bool = False,
    review_reason: str = "",
    metadata: dict = None,
):
    """บันทึก event ลง ai_chat_events table สำหรับ Dashboard"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        ctx = ctx or {}
        payload = {
            "psid":                   psid,
            "event_type":             event_type,
            "role":                   "user" if event_type == "user_message" else "assistant",
            "message":                (message or "")[:1000],
            "bot_reply":              (bot_reply or "")[:1000],
            "intent":                 intent or "",
            "lead_stage":             ctx.get("lead_stage") or ctx.get("_lead_stage") or "cold",
            "destination":            ctx.get("destination") or "",
            "country":                ctx.get("country") or "",
            "country_id":             str(ctx.get("country_id") or ""),
            "city_hint":              ctx.get("city_hint") or "",
            "selected_tour_code_real":ctx.get("selected_tour_code") or "",
            "selected_web_code":      ctx.get("selected_tour_web_code") or "",
            "selected_tour_name":     ctx.get("selected_tour_name") or "",
            "selected_tour_url":      ctx.get("selected_tour_url") or "",
            "selected_tour_airline":  ctx.get("selected_tour_airline") or "",
            "ad_id":                  ctx.get("ad_id") or "",
            "ad_ref":                 ctx.get("ad_ref") or "",
            "ad_title":               ctx.get("ad_title") or "",
            "needs_review":           needs_review,
            "review_reason":          review_reason or "",
        }
        if metadata:
            payload["metadata"] = metadata
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/ai_chat_events",
            json=payload,
            headers=_sb_headers(),
            timeout=8,
        )
        if not resp.ok:
            logger.warning(f"log_chat_event {event_type}: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"log_chat_event error: {e}")


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

def fetch_fb_profile(psid: str) -> dict:
    """ดึงชื่อลูกค้าจาก Facebook Graph API โดยใช้ PSID
    คืน dict: {name, first_name, last_name} หรือ {} ถ้าดึงไม่ได้
    """
    if not FB_PAGE_TOKEN or not psid:
        return {}
    try:
        resp = requests.get(
            f"https://graph.facebook.com/v19.0/{psid}",
            params={
                "fields": "name,first_name,last_name,profile_pic",
                "access_token": FB_PAGE_TOKEN,
            },
            timeout=6,
        )
        if resp.ok:
            data = resp.json()
            if "name" in data:
                logger.info(f"✅ FB profile fetched: {data['name']}")
                return data
        logger.warning(f"fetch_fb_profile {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        logger.warning(f"fetch_fb_profile error: {e}")
    return {}


def notify_line(message: str):
    """ส่งแจ้งเตือนผ่าน LINE Messaging API (push message)
    - ส่งหา LINE_ADMIN_ID (แอดมิน personal) เสมอถ้าตั้งไว้
    - ส่งหา LINE_GROUP_ID (กลุ่มทีม) ถ้าตั้งไว้
    """
    if not LINE_CHANNEL_TOKEN:
        logger.warning("LINE_CHANNEL_TOKEN not set — skip notify")
        return

    targets = []
    if LINE_ADMIN_ID:
        targets.append(LINE_ADMIN_ID)
    if LINE_GROUP_ID and LINE_GROUP_ID not in targets:
        targets.append(LINE_GROUP_ID)

    if not targets:
        logger.warning("No LINE target (LINE_ADMIN_ID / LINE_GROUP_ID) — skip notify")
        return

    for target in targets:
        try:
            resp = requests.post(
                "https://api.line.me/v2/bot/message/push",
                headers={
                    "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={
                    "to": target,
                    "messages": [{"type": "text", "text": message}],
                },
                timeout=10,
            )
            if resp.status_code == 200:
                label = "group" if target == LINE_GROUP_ID else "admin"
                logger.info(f"📨 LINE push sent → {label} ✅")
            else:
                logger.error(f"❌ LINE push failed {resp.status_code}: {resp.text[:300]}")
        except Exception as e:
            logger.error(f"notify_line error ({target[:12]}): {e}")


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


def save_customer_profile_supabase(psid: str, profile: dict):
    """Upsert customer profile (Meta name + pic) to Supabase customers table"""
    if not SUPABASE_URL or not SUPABASE_KEY or not profile:
        return
    try:
        now_iso = datetime.utcnow().isoformat()
        payload = {
            "psid":               psid,
            "full_name":          profile.get("full_name", ""),
            "first_name":         profile.get("first_name", ""),
            "last_name":          profile.get("last_name", ""),
            "profile_pic":        profile.get("profile_pic", ""),
            "profile_updated_at": now_iso,
        }
        if profile.get("full_name"):
            payload["name"] = profile["full_name"]
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/customers",
            json=payload,
            headers=_sb_headers(prefer_upsert=True),
            timeout=10,
        )
        if resp.ok or resp.status_code == 201:
            logger.info(f"✅ Customer profile saved: {psid} → {profile.get('full_name','')}")
        else:
            logger.warning(f"save_customer_profile: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.error(f"save_customer_profile_supabase error: {e}")


def fetch_customers_batch(psid_list: list) -> dict:
    """Batch fetch customer profiles from Supabase. Returns {psid: customer_dict}"""
    if not psid_list or not SUPABASE_URL or not SUPABASE_KEY:
        return {}
    try:
        psid_in = "(" + ",".join(psid_list) + ")"
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/customers",
            params={
                "psid": f"in.{psid_in}",
                "select": "psid,name",
            },
            headers=_sb_headers(),
            timeout=10,
        )
        if resp.ok:
            return {c["psid"]: c for c in resp.json() if "psid" in c}
        logger.warning(f"fetch_customers_batch: {resp.status_code}")
    except Exception as e:
        logger.error(f"fetch_customers_batch error: {e}")
    return {}


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



# Thai month abbreviation map
_TH_MONTH_MAP = {
    "ม.ค": "ม.ค.", "ก.พ": "ก.พ.", "มี.ค": "มี.ค.", "เม.ย": "เม.ย.",
    "พ.ค": "พ.ค.", "มิ.ย": "มิ.ย.", "ก.ค": "ก.ค.", "ส.ค": "ส.ค.",
    "ก.ย": "ก.ย.", "ต.ค": "ต.ค.", "พ.ย": "พ.ย.", "ธ.ค": "ธ.ค.",
}
_TH_MONTH_ORDER = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
                   "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
_TH_MONTH_WORDS = {
    "มกราคม": "ม.ค.", "กุมภาพันธ์": "ก.พ.", "มีนาคม": "มี.ค.",
    "เมษายน": "เม.ย.", "พฤษภาคม": "พ.ค.", "มิถุนายน": "มิ.ย.",
    "กรกฎาคม": "ก.ค.", "สิงหาคม": "ส.ค.", "กันยายน": "ก.ย.",
    "ตุลาคม": "ต.ค.", "พฤศจิกายน": "พ.ย.", "ธันวาคม": "ธ.ค.",
    "มกรา": "ม.ค.", "กุมภา": "ก.พ.", "มีนา": "มี.ค.",
    "เมษา": "เม.ย.", "พฤษภา": "พ.ค.", "มิถุนา": "มิ.ย.",
    "กรกฎา": "ก.ค.", "สิงหา": "ส.ค.", "กันยา": "ก.ย.",
    "ตุลา": "ต.ค.", "พฤศจิกา": "พ.ย.", "ธันวา": "ธ.ค.",
    "เดือน 1": "ม.ค.", "เดือน 2": "ก.พ.", "เดือน 3": "มี.ค.",
    "เดือน 4": "เม.ย.", "เดือน 5": "พ.ค.", "เดือน 6": "มิ.ย.",
    "เดือน 7": "ก.ค.", "เดือน 8": "ส.ค.", "เดือน 9": "ก.ย.",
    "เดือน 10": "ต.ค.", "เดือน 11": "พ.ย.", "เดือน 12": "ธ.ค.",
}

def _extract_month_from_date(date_str: str) -> str:
    """คืน abbreviated month เช่น 'ก.ค.' จาก '2-6 ก.ค. 69'"""
    for abbr in _TH_MONTH_MAP:
        if abbr in date_str:
            return _TH_MONTH_MAP[abbr]
    return ""

def detect_departure_month_from_text(text: str) -> str | None:
    """Rule-based — ตรวจว่า user พิมพ์เดือนไหน คืน abbreviated month เช่น 'ก.ค.'"""
    t = text.strip()
    # Full name / partial Thai words
    for word, abbr in _TH_MONTH_WORDS.items():
        if word in t:
            return abbr
    # Abbreviated form already
    for abbr in _TH_MONTH_ORDER:
        if abbr.rstrip(".") in t or abbr in t:
            return abbr
    # Digit month "7", "07"
    m = re.search(r"เดือน\s*(\d{1,2})", t)
    if m:
        idx = int(m.group(1))
        if 1 <= idx <= 12:
            return _TH_MONTH_ORDER[idx - 1]
    # Just a digit 1-12 on its own
    m2 = re.fullmatch(r"\s*(\d{1,2})\s*", t)
    if m2:
        idx = int(m2.group(1))
        if 1 <= idx <= 12:
            return _TH_MONTH_ORDER[idx - 1]
    return None


def fetch_departure_structured(tour_url: str) -> dict:
    """Parse departure table from detail page into structured monthly groups.

    Returns:
        {
          "rows": [{"date": "2-6 ก.ค. 69", "adult": 19999, "child_no_bed": 17999, "raw": "..."}, ...],
          "by_month": {"ก.ค.": [...], "ส.ค.": [...]},
          "month_order": ["ก.ค.", "ส.ค."],
          "fee_summary": "ทิปไกด์ 2,000 บาท | วีซ่า: รวม",
        }
    """
    result = {"rows": [], "by_month": {}, "month_order": [], "fee_summary": ""}
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; TourFiremaiBot/1.0)"}
        resp = requests.get(tour_url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        text = resp.text

        # ── Extract fee info ──────────────────────────────────────────────
        fee_parts = []
        tip_m = re.search(r"(?:ทิป|tip)[^0-9]{0,20}([\d,]+)\s*(?:บาท|baht)?", text, re.IGNORECASE)
        if tip_m:
            fee_parts.append(f"ทิปไกด์ {tip_m.group(1)} บาท/ท่าน")
        visa_m = re.search(r"(?:วีซ่า|visa)[^:]{0,30}:?\s*([^\n<]{1,40})", text, re.IGNORECASE)
        if visa_m:
            v = visa_m.group(1).strip()[:40]
            fee_parts.append(f"วีซ่า: {v}")
        single_m = re.search(r"(?:พักเดี่ยว|single)[^0-9]{0,20}([\d,]+)", text, re.IGNORECASE)
        if single_m:
            fee_parts.append(f"พักเดี่ยวเพิ่ม {single_m.group(1)} บาท")
        deposit_m = re.search(r"(?:มัดจำ|deposit)[^0-9]{0,20}([\d,]+)", text, re.IGNORECASE)
        if deposit_m:
            fee_parts.append(f"มัดจำ {deposit_m.group(1)} บาท")
        result["fee_summary"] = " | ".join(fee_parts) if fee_parts else ""

        # ── Parse departure rows from table ───────────────────────────────
        DATE_PAT = re.compile(
            r"(\d{1,2}[-–]\d{1,2}\s*(?:ม\.ค|ก\.พ|มี\.ค|เม\.ย|พ\.ค|มิ\.ย|ก\.ค|ส\.ค|ก\.ย|ต\.ค|พ\.ย|ธ\.ค)\.?(?:\s*[-–]\s*\d{1,2}\s*(?:ม\.ค|ก\.พ|มี\.ค|เม\.ย|พ\.ค|มิ\.ย|ก\.ค|ส\.ค|ก\.ย|ต\.ค|พ\.ย|ธ\.ค)\.?)?(?:\s*\d{2,4})?)"
        )
        PRICE_PAT = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{4,6})\s*(?:บาท|฿)?")

        rows = []
        # Try table rows first
        for tr in soup.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            row_text = " | ".join(cells)
            date_m = DATE_PAT.search(row_text)
            if not date_m:
                continue
            date_str = date_m.group(1).strip()
            prices = [int(p.replace(",", "")) for p in PRICE_PAT.findall(row_text)
                      if 3000 <= int(p.replace(",", "")) <= 200000]
            if not prices:
                continue
            row = {
                "date": date_str,
                "adult": prices[0] if prices else None,
                "child_no_bed": prices[1] if len(prices) > 1 else None,
                "raw": row_text[:120],
            }
            rows.append(row)

        # Fallback: line-by-line scan if table parsing got nothing
        if not rows:
            for line in text.split("\n"):
                date_m = DATE_PAT.search(line)
                if not date_m:
                    continue
                prices = [int(p.replace(",", "")) for p in PRICE_PAT.findall(line)
                          if 3000 <= int(p.replace(",", "")) <= 200000]
                if not prices:
                    continue
                rows.append({
                    "date": date_m.group(1).strip(),
                    "adult": prices[0],
                    "child_no_bed": prices[1] if len(prices) > 1 else None,
                    "raw": line.strip()[:120],
                })

        # De-duplicate by date
        seen_dates = set()
        unique_rows = []
        for r in rows:
            if r["date"] not in seen_dates:
                seen_dates.add(r["date"])
                unique_rows.append(r)
        result["rows"] = unique_rows

        # Group by month (order by _TH_MONTH_ORDER)
        by_month: dict = {}
        for r in unique_rows:
            mo = _extract_month_from_date(r["date"])
            if mo:
                by_month.setdefault(mo, []).append(r)
        # Sort months in calendar order
        sorted_months = [m for m in _TH_MONTH_ORDER if m in by_month]
        result["by_month"] = by_month
        result["month_order"] = sorted_months

    except Exception as e:
        logger.debug(f"fetch_departure_structured error {tour_url}: {e}")
    return result


def format_departure_for_chat(structured: dict, max_rows: int = 5) -> str:
    """สร้าง tour_data string จาก structured departure — ถ้า rows > max_rows สรุปเป็นเดือน"""
    rows = structured.get("rows", [])
    by_month = structured.get("by_month", {})
    month_order = structured.get("month_order", [])
    fee = structured.get("fee_summary", "")

    parts = []
    if fee:
        parts.append(f"[ค่าใช้จ่ายสำคัญ]\n{fee}")

    if len(rows) == 0:
        parts.append("[วันเดินทาง] ไม่พบตารางวันเดินทางในหน้านี้ — กรุณาดูที่ลิงก์โปรแกรม")
    elif len(rows) <= max_rows:
        lines = ["[รอบเดินทางทั้งหมด]"]
        for r in rows:
            line = f"- {r['date']}"
            if r.get("adult"):
                line += f" ผู้ใหญ่ {r['adult']:,}"
            if r.get("child_no_bed"):
                line += f" / เด็กไม่มีเตียง {r['child_no_bed']:,}"
            lines.append(line)
        parts.append("\n".join(lines))
    else:
        # Summarize by month
        lines = ["[รอบเดินทาง — สรุปตามเดือน]"]
        for mo in month_order:
            month_rows = by_month[mo]
            date_strs = [r["date"].split()[0] + " " + mo for r in month_rows]
            # Try to show date ranges concisely
            date_display = ", ".join(d.split()[0] for d in [r["date"] for r in month_rows][:6])
            lines.append(f"- {mo}: {date_display}")
        parts.append("\n".join(lines))
        parts.append(f"[INSTRUCTION_FOR_BOT] มีทั้งหมด {len(rows)} รอบ ใน {len(month_order)} เดือน "
                     f"({', '.join(month_order)}) ให้ถามลูกค้าว่าสนใจเดือนไหน "
                     f"ห้ามยกตารางทั้งหมด ให้สรุปเป็นเดือนแล้วถามทีละ 1 คำถาม")

    return "\n\n".join(parts)

def _fetch_tour_code_real_quick(url: str) -> str:
    """On-demand fetch of tour_code_real from detail page — called when DB has null."""
    if not url:
        return ""
    try:
        import re as _re
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; TourFiremaiBot/1.0)"}, timeout=6)
        m = _re.search(r'tcode=([A-Z0-9\-]+)', resp.text)
        if m:
            return m.group(1).strip()
        # fallback: label รหัสทัวร์
        m2 = _re.search(r'รหัสทัวร์[^<]{0,60}<p[^>]*class="txt-pd-l"[^>]*>([^<]+)</p>', resp.text)
        if m2:
            return m2.group(1).strip()
    except Exception as e:
        logger.debug(f"_fetch_tour_code_real_quick error {url}: {e}")
    return ""


def _update_tour_code_real_bg(url: str, web_code: str, tour_code_real: str):
    """Update tour_code_real in Supabase in background thread."""
    if not SUPABASE_URL or not SUPABASE_KEY or not tour_code_real:
        return
    try:
        import urllib.request as _ur
        payload = json.dumps({"tour_code_real": tour_code_real}).encode()
        req = _ur.Request(
            f"{SUPABASE_URL}/rest/v1/tours?tour_code=eq.{web_code}",
            data=payload, method="PATCH",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            }
        )
        with _ur.urlopen(req, timeout=5):
            logger.info(f"✅ Updated tour_code_real={tour_code_real} for {web_code}")
    except Exception as e:
        logger.debug(f"_update_tour_code_real_bg error: {e}")


def fetch_tours_from_db(country_id: str, city_hint: str = None,
                        budget_max: int = None,
                        search_mode: str = "normal") -> list:
    """Query Supabase `tours` table.
    Returns list of dicts: name, url, tour_code, price_min, airline, departure_dates
    Filters by city, budget, and/or search_mode (normal|faimai|any).
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        params = {
            "country_id": f"eq.{country_id}",
            "select":     "name,url,tour_code,web_code,tour_code_real,price_min,promo_price,original_price,discount_amount,discount_percent,discount_text,promo_badge,airline,departure_dates,is_faimai,tip_fee,visa_fee,visa_status,single_supplement",
            "order":      "price_min.asc.nullslast",
            "limit":      "60",
        }
        # Filter by source type
        if search_mode == "faimai":
            params["is_faimai"] = "eq.true"
            params["is_active"] = "eq.true"   # ไม่เอา stale faimai tours
        elif search_mode == "normal":
            params["source_type"] = "eq.normal"
        # search_mode="any" → no filter
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

def fetch_tours(country_id: str, city_hint: str = None, budget_max: int = None,
                search_mode: str = "normal") -> str:
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
            _real = t.get("tour_code_real", "") or ""
            _wc   = t.get("web_code", "") or t.get("tour_code", "") or t.get("code", "") or ""
            if _real:
                line += f"\n   🏷 รหัสทัวร์: {_real}"
                if _wc:
                    line += f"\n   🔑 รหัสเว็บ: {_wc}"
            elif _wc:
                line += f"\n   🔑 รหัสเว็บ: {_wc}"
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
                "web_code": t.get("web_code", "") or t.get("tour_code", ""),
                "tour_code_real": t.get("tour_code_real", ""),
                "airline": t.get("airline", ""),
                "name": t["name"],
                "price_min": t.get("price_min"),
                "url": link,
            })
        return "\n\n".join(parts), meta

    # ── 1. Try Supabase DB ────────────────────────────────────────────────────
    db_tours = fetch_tours_from_db(country_id, city_hint=city_hint, budget_max=budget_max, search_mode=search_mode)
    if db_tours:
        logger.info(f"DB hit: {len(db_tours)} tours for country={country_id} city={city_hint}")
        # Check if DB has price/dates data (v2 scraper)
        has_price_data = any(t.get("price_min") for t in db_tours)
        if has_price_data:
            # Format directly from DB — faimai tours show discount/fee info
            # Sort faimai by discount_amount desc, then price asc
            display_tours = db_tours[:8]  # fetch extra, will slice to 6 after sort
            if search_mode == "faimai":
                def _faimai_sort_key(t):
                    disc = t.get("discount_amount") or 0
                    price = t.get("promo_price") or t.get("price_min") or 999999
                    return (-disc, price)
                display_tours = sorted(display_tours, key=_faimai_sort_key)[:6]
            else:
                display_tours = display_tours[:6]

            # ── On-demand fetch tour_code_real for tours missing it ──────────
            tours_need_code = [t for t in display_tours if not t.get("tour_code_real") and t.get("url")]
            if tours_need_code:
                def _fetch_one(t):
                    rc = _fetch_tour_code_real_quick(t.get("url", ""))
                    if rc:
                        t["tour_code_real"] = rc
                        wc = t.get("web_code") or t.get("tour_code") or ""
                        threading.Thread(target=_update_tour_code_real_bg, args=(t["url"], wc, rc), daemon=True).start()
                    return t
                with ThreadPoolExecutor(max_workers=min(len(tours_need_code), 4)) as _ex:
                    list(_ex.map(_fetch_one, tours_need_code))

            parts = []
            meta = []
            for i, t in enumerate(display_tours):
                promo  = t.get("promo_price") or t.get("price_min")
                price_str = f"{promo:,} บาท" if promo else "ติดต่อสอบถาม"
                dates_str = t.get("departure_dates") or "ติดต่อเช็กวัน"
                airline_str = t.get("airline") or ""
                web_code_str  = t.get("web_code", "") or t.get("tour_code", "") or ""
                real_code_str = t.get("tour_code_real", "") or ""
                line = f"📌 {t['name']}"
                if airline_str:
                    line += f" ({airline_str})"
                if real_code_str:
                    line += f"\n   🏷 รหัสทัวร์: {real_code_str}"
                    if web_code_str:
                        line += f"\n   🔑 รหัสเว็บ: {web_code_str}"
                elif web_code_str:
                    line += f"\n   🔑 รหัสเว็บ: {web_code_str}"
                    line += f"\n   🏷 รหัสทัวร์: (ตรวจสอบหน้าโปรแกรม)"
                # Faimai: show discount info
                if t.get("is_faimai"):
                    orig = t.get("original_price")
                    disc_amt = t.get("discount_amount")
                    disc_pct = t.get("discount_percent")
                    disc_txt = t.get("discount_text")
                    if orig and promo and disc_amt:
                        line += f"\n   ~~ราคาเดิม: {orig:,} บาท~~"
                        line += f"\n   🔥 ราคาโปร: {price_str}"
                        pct_str = f" ({disc_pct:.0f}%)" if disc_pct else ""
                        line += f"\n   ลดทันที: {disc_amt:,} บาท{pct_str}"
                    elif disc_txt:
                        line += f"\n   🔥 ราคาโปร: {price_str}"
                        line += f"\n   โปรโมชัน: {disc_txt}"
                    else:
                        line += f"\n   🔥 ราคาโปร: {price_str}"
                    # Fee summary
                    tip = t.get("tip_fee")
                    visa = t.get("visa_status")
                    if tip:
                        est = (promo or 0) + tip + (t.get("visa_fee") or 0)
                        line += f"\n   ทิปไกด์: {tip:,} บาท/คน"
                        if visa:
                            line += f"  |  วีซ่า: {visa}"
                        line += f"\n   💳 จ่ายจริงประมาณ: {est:,} บาท/คน"
                    else:
                        line += f"\n   ⚠️ ทิปไกด์/ค่าบังคับ: ต้องเช็ก PDF ก่อน"
                else:
                    line += f"\n   💰 ราคาเริ่ม: {price_str}"
                line += f"\n   📅 วันเดินทาง: {dates_str}"
                line += f"\n   🔗 [LINK:{t['url']}]"
                parts.append(line)
                meta.append({
                    "index": i + 1,
                    "tour_code": t.get("tour_code", ""),
                    "web_code": t.get("web_code", "") or t.get("tour_code", ""),
                    "tour_code_real": t.get("tour_code_real", ""),
                    "airline": t.get("airline", ""),
                    "name": t["name"],
                    "price_min": promo,
                    "url": t.get("url", ""),
                    "is_faimai": t.get("is_faimai", False),
                    "discount_amount": t.get("discount_amount"),
                    "tip_fee": t.get("tip_fee"),
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
    departure_ctx_section = ""
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
        if ctx.get("_fallback_reason") == "options_expired":
            parts.append(
                "⚠️ รายการโปรแกรมก่อนหน้าหมดอายุจาก Redis แล้ว — "
                "บอกลูกค้าสั้นๆ ว่า 'ขออภัยค่ะ รายการก่อนหน้าหายจากระบบ ค้นใหม่ให้เลยนะคะ' "
                "แล้วแสดงผลใหม่จากข้อมูลที่ดึงมาตอนนี้ — ห้ามถามเริ่มต้นใหม่"
            )
            ctx.pop("_fallback_reason", None)

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
            known_rules.append(f"❌ ห้ามทับศัพท์หรือแปลชื่อลูกค้า — ถ้าชื่อเป็นภาษาอังกฤษให้ใช้ตรงๆ เช่น คุณ Supakit ไม่ใช่ คุณสุพากิตย์")
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
                next_step = "→ step ถัดไป: ขอชื่อ+เบอร์โทรติดต่อ (ปิดจบใน Facebook เลย)"
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

        # ── PATCH 6: DEPARTURE CONTEXT ─────────────────────────────────────────
        departure_ctx_section = ""
        pending = ctx.get("pending_action", "")
        if pending in ("wait_departure_month", "wait_departure_date"):
            avail_months = ctx.get("available_departure_months") or []
            selected_month = ctx.get("last_selected_departure_month", "")
            tour_name = ctx.get("selected_tour_name", "โปรแกรมที่เลือก")

            if pending == "wait_departure_month" and avail_months:
                months_str = " / ".join(avail_months)
                departure_ctx_section = (
                    "\n══════════════════════════════════\n"
                    "📅 สถานะ: รอลูกค้าเลือกเดือนเดินทาง\n"
                    "══════════════════════════════════\n"
                    f"โปรแกรม: {tour_name}\n"
                    f"เดือนที่มีรอบเดินทาง: {months_str}\n"
                    "\n⚠️ กฎสำคัญ (ห้ามละเมิด):\n"
                    "❌ ห้ามถามประเทศหรือโปรแกรมซ้ำ — ลูกค้าเลือกโปรแกรมแล้ว\n"
                    "❌ ห้ามแสดงตารางวันเดินทางทั้งหมด — รอให้ลูกค้าเลือกเดือนก่อน\n"
                    "❌ ห้ามถามมากกว่า 1 คำถาม\n"
                    "✅ ถามเพียง: ลูกค้าสะดวกเดือนไหน?\n"
                    f"✅ ตัวอย่าง: 'มีรอบเดินทาง {months_str} นะคะ สะดวกเดือนไหนคะ? 😊'\n"
                )
            elif pending == "wait_departure_date":
                departure_ctx_section = (
                    "\n══════════════════════════════════\n"
                    f"📅 สถานะ: รอลูกค้าเลือกวันเดินทาง"
                    + (f" (เดือน {selected_month})" if selected_month else "") + "\n"
                    "══════════════════════════════════\n"
                    f"โปรแกรม: {tour_name}\n"
                    + (f"เดือนที่เลือก: {selected_month}\n" if selected_month else "")
                    + "\n⚠️ กฎสำคัญ:\n"
                    "❌ ห้ามถามประเทศหรือโปรแกรมซ้ำ\n"
                    "✅ แสดงรอบวันเดินทางเฉพาะเดือนที่ลูกค้าเลือก แล้วให้เลือก 1 รอบ\n"
                    "✅ ถ้าไม่มีข้อมูลรอบ → ถามว่าต้องการวันไหนในเดือนนั้น\n"
                )

    return f"""คุณคือ AI Travel Sales Assistant ของเพจ รวมทัวร์ไฟไหม้ — ฉลาด อบอุ่น เชี่ยวชาญด้านทัวร์
บริษัท อัพ-โอเปอเรชั่น จำกัด | เว็บ: www.tourfiremai.com | LINE: @tourfiremai
วันนี้: {today}
{ctx_section}{departure_ctx_section}
══════════════════════════════════
Conversation Brain — หัวใจสำคัญ
══════════════════════════════════
คุณคือ AI Travel Assistant ที่ฉลาด ไม่ใช่ Search Bot
เป้าหมาย: คุยกับลูกค้าให้ลื่นเหมือน ChatGPT — เข้าใจ ตอบ ถาม ค้นหา ตามลำดับที่เหมาะสม

กฎทอง:
✅ ถ้าลูกค้าพูดกว้างๆ → ตอบด้วยความรู้ทั่วไป + ถามต่อ 1 คำถาม
✅ ถ้าลูกค้าระบุประเทศ/เมืองชัดเจน → ระบบค้นหาทัวร์ให้อัตโนมัติ
❌ ห้ามโยนโปรแกรม Top 3 ทันทีที่ลูกค้าพูดกว้างๆ ยังไม่รู้ปลายทาง
❌ ห้ามใช้คำว่า "น้องแอดมิน" ในทุกกรณี
❌ ห้ามใช้ข้อความ template ซ้ำๆ แข็งๆ

ตัวอย่างการตอบแบบ QUALIFICATION MODE (ไม่มีข้อมูลทัวร์ส่งมาให้):
"สนใจทัวร์ไฟไหม้ครับ"
→ "ทัวร์ไฟไหม้เหมาะมากถ้าพร้อมเดินทางเร็วค่ะ 🔥 ตอนนี้ดีลดีๆ มักอยู่ในกลุ่มจีน เวียดนาม เกาหลี ไต้หวัน อยากได้งบต่อคนประมาณเท่าไหร่คะ?"
"มีโปรอะไรบ้าง"
→ "มีหลายกลุ่มเลยค่ะ ทั้งจีน เวียดนาม เกาหลี ญี่ปุ่น ไต้หวัน ฮ่องกง อยากได้งบต่อคนประมาณเท่าไหร่คะ?"
"งบ 7990 มีประเทศไหนบ้าง"
→ "งบ 7,990 บาท โอกาสดีในกลุ่มนี้ค่ะ: จีน (เฉิงตู/คุนหมิง/จางเจียเจี้ย) • เวียดนาม (ดานัง/ฮานอย) • ไต้หวัน สนใจประเทศไหนเป็นพิเศษคะ?"
"สนใจญี่ปุ่นมีไหมครับ"
→ "มีค่ะ ญี่ปุ่นมีหลายเส้นทาง เช่น โตเกียว โอซาก้า ฮอกไกโด ฟุกุโอกะค่ะ อยากได้งบต่อคนประมาณเท่าไหร่คะ? 😊"

══════════════════════════════════
บุคลิกและสไตล์การตอบ
══════════════════════════════════
- พูดภาษาไทยสวย เป็นกันเองแต่มืออาชีพ ลงท้ายด้วย ค่ะ / นะคะ / คะ เสมอ
- ❌ ห้ามใช้คำลงท้าย "ครับ" เด็ดขาด — ใช้เฉพาะ ค่ะ / คะ / นะคะ / จ้ะ เท่านั้น
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

"ได้เลยค่ะ สรุปให้ทีมงานเช็กตัวนี้เลยนะคะ 🔍

โปรแกรม: [ชื่อโปรแกรม]
รหัส: [รหัส]
วันเดินทาง: [วัน]
จำนวน: [จำนวนคน]
ราคาเริ่มต้น: [ราคา]

ทีมงานจะเช็กที่นั่งและราคาอัปเดตให้อีกครั้งค่ะ
ขอชื่อผู้ติดต่อและเบอร์โทรไว้ให้ทีมงานติดต่อกลับได้เลยนะคะ?"

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
กฎรหัสทัวร์ — ต้องแสดงทุกครั้ง (สำคัญมาก)
══════════════════════════════════
ทุกครั้งที่เสนอโปรแกรมทัวร์ → ต้องแสดง รหัสที่มี เสมอ
มี 3 รหัสคนละตัว: อย่าปนกัน
  🏷 รหัสทัวร์จริง = เช่น ZGNRT-2618VZ (จาก label "รหัสทัวร์" ในหน้าโปรแกรม)
  🔑 รหัสเว็บ = เช่น ap241533 (จาก URL tourfiremai.com/tour/ap241533)
  ✈️ สายการบิน = เช่น VZ, XJ, TG (ชื่อย่อสายการบิน)

❌ ห้ามนำ สายการบิน (VZ/XJ) ไปใส่ใน "รหัสทัวร์" เด็ดขาด
❌ ห้ามเรียก ap241533 ว่า "รหัสทัวร์" — เรียกว่า "รหัสเว็บ" เท่านั้น

รูปแบบที่ถูกต้อง (มีรหัสทัวร์จริง):
  ✈️ [ชื่อโปรแกรม] (สายการบิน VZ)
  🏷 รหัสทัวร์: ZGNRT-2618VZ
  🔑 รหัสเว็บ: ap241533
  💰 ราคาเริ่ม: [ราคา] บาท
  📅 วันเดินทาง: [วัน]
  🔗 [ลิงก์]

รูปแบบ fallback (ยังไม่มีรหัสทัวร์จริง):
  ✈️ [ชื่อโปรแกรม] (สายการบิน VZ)
  🔑 รหัสเว็บ: ap241533
  🏷 รหัสทัวร์จริง: กำลังเช็กจากหน้าโปรแกรม
  💰 ราคาเริ่ม: [ราคา] บาท

เมื่อลูกค้าเลือกโปรแกรม:
  → ยืนยัน: "รับทราบค่ะ สนใจ [ชื่อโปรแกรม] — รหัสทัวร์ [รหัสทัวร์จริง] ใช่ไหมคะ? 😊"
  → ถ้าไม่มีรหัสทัวร์จริง: ใช้รหัสเว็บแทน "รหัสเว็บ [ap...]"

❌ ห้ามเสนอโปรแกรมโดยไม่มีรหัสใดเลย
❌ ห้ามส่งต่อเซลล์โดยไม่มี รหัสทัวร์จริงหรือรหัสเว็บ

══════════════════════════════════
กฎราคา — ห้ามเดาราคาแต่ละรอบ (สำคัญมาก)
══════════════════════════════════
ราคาในระบบ (price_min) = ราคาเริ่มต้นต่ำสุดของโปรแกรม ไม่ใช่ราคาของทุกรอบเดินทาง

❌ ห้ามเด็ดขาด: นำ price_min ไปใส่ทุกวันเดินทาง เช่น:
   "14 พ.ค. → 9,888 บาท / 15 พ.ค. → 9,888 บาท / 17 พ.ค. → 9,888 บาท"
   (ความจริงแต่ละรอบอาจต่างกัน เช่น 9,888 / 10,888 / 11,888)

✅ ถูกต้อง — ถ้ายังไม่ได้อ่าน PDF:
   "ราคาเริ่มต้น 9,888 บาท/ท่าน (ราคาต่ำสุด แต่ละรอบอาจต่างกัน ต้องเช็กยืนยัน)"

✅ ถูกต้อง — ถ้าลูกค้าถามราคาเฉพาะรอบ/วัน:
   → trigger action=detail_pdf เพื่ออ่าน PDF ก่อน แล้วแสดงราคาจริงรายรอบจาก PDF
   → ห้ามตอบราคารายรอบโดยไม่มีข้อมูลจาก PDF

กฎ: ถ้าลูกค้าพูดว่า "แต่ละรอบ" / "แต่ละวัน" / "ราคาเดือน X" / "รอบไหนราคาเท่าไหร่"
    → ต้องอ่าน PDF ก่อน ห้ามเดาหรือใช้ price_min กับทุกวัน

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

【STEP 4】ขอข้อมูลติดต่อ (ชื่อ + เบอร์โทร)
  → เมื่อได้วันเดินทาง + จำนวนคนแล้ว:
  "ขอชื่อผู้ติดต่อและเบอร์โทรไว้ให้ทีมงานยืนยันที่นั่งด้วยค่ะ"

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
  📱 เบอร์โทร: [เบอร์]
  ──────────────────
  แล้วแจ้ง: "ส่งข้อมูลให้ทีมงานแล้วค่ะ 😊 จะติดต่อกลับภายใน 15-30 นาที เพื่อยืนยันที่นั่งและแจ้งรายละเอียดการชำระเงินค่ะ"

หมายเหตุ: AI ไม่สามารถยืนยันที่นั่ง รับเงิน หรือยืนยันราคา final ได้ — ทีมงานจะดำเนินการ

══════════════════════════════════
เมื่อลูกค้าขอคุยกับคนจริง / เซลล์ / แอดมิน
══════════════════════════════════
ตอบทันที: "รับทราบค่ะ ส่งข้อมูลให้ทีมงานแล้วค่ะ จะติดต่อกลับทาง Facebook นี้เร็วๆ นี้เลยนะคะ 😊"
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
→ ตอบทันที: "ได้รับแล้วค่ะ ขอบคุณนะคะ 🙏 แจ้งทีมงานตรวจสอบและยืนยันการจองให้เลยค่ะ กรุณารอสักครู่นะคะ"

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
"ตอนนี้ยังไม่เจอโอซาก้าที่ตรงงบ 40,000 พอดีค่ะ แนะนำได้ 2 ทางค่ะ:
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

def fetch_faimai_tours(country_filter: str = None) -> str:
    """ดึงทัวร์ไฟไหม้จาก tourfiremai.com/faimai"""
    try:
        import requests as _req
        resp = _req.get("https://www.tourfiremai.com/faimai", timeout=10,
                        headers={"User-Agent": "Mozilla/5.0"})
        html = resp.text
    except Exception as e:
        logger.error(f"fetch_faimai error: {e}")
        return "ขออภัยค่ะ ดึงข้อมูลทัวร์ไฟไหม้ไม่ได้ตอนนี้"

    import re as _re
    blocks = _re.split(r'<div class="b-one-pg', html)
    tours = []
    for block in blocks[1:]:
        url_m   = _re.search(r'href="(https://www\.tourfiremai\.com/tour/[^"]+)"', block)
        name_m  = _re.search(r'<h3>(.*?)</h3>', block, _re.DOTALL)
        code_m  = _re.search(r'<i class="fa-solid fa-tag"></i></span> <span>(.*?)</span>', block)
        days_m  = _re.search(r'(\d+)วัน', block)
        date_m  = _re.search(r'<i class="fa-solid fa-calendar-days"></i></span>\s*<span>(.*?)</span>', block, _re.DOTALL)
        airline_m = _re.search(r'alt="([^"]{3,40})" class="size-img">', block)
        disc_m  = _re.search(r'nb-dcprice-pgt">(.*?)<span>', block, _re.DOTALL)
        price_m = _re.search(r'txt-price">[^<]*<span class="txt-price-full-box1">.*?</span>(.*?)</p>', block, _re.DOTALL)

        if not url_m or not name_m:
            continue
        name    = _re.sub(r'<[^>]+>', '', name_m.group(1)).strip()
        days    = days_m.group(1) if days_m else "?"
        date    = _re.sub(r'\s+', ' ', date_m.group(1).strip()) if date_m else ""
        airline = airline_m.group(1).strip() if airline_m else ""
        disc    = _re.sub(r'<[^>]+>', '', disc_m.group(1)).strip() if disc_m else ""
        price   = _re.sub(r'[^\d,]', '', price_m.group(1).strip()) if price_m else ""
        url     = url_m.group(1)

        # กรองตามประเทศถ้ามี
        if country_filter:
            if not any(k.lower() in name.lower() for k in country_filter.split()):
                continue

        tours.append({"name": name, "days": days, "date": date,
                      "airline": airline, "disc": disc, "price": price, "url": url})

    if not tours:
        return "ขณะนี้ยังไม่มีทัวร์ไฟไหม้ที่ตรงกับที่ค้นหาค่ะ"

    lines = ["🔥 **ทัวร์ไฟไหม้ราคาพิเศษ** จาก tourfiremai.com/faimai\n"]
    for i, t in enumerate(tours[:6], 1):
        line = f"{i}. {t['name']}"
        if t['days']: line += f" ({t['days']}วัน)"
        if t['date']: line += f"\n   📅 {t['date']}"
        if t['airline']: line += f" | ✈️ {t['airline']}"
        if t['price']: line += f"\n   💰 เริ่มต้น {t['price']} บาท/ท่าน"
        if t['disc']: line += f" (ลด {t['disc']} บาท!)"
        line += f"\n   🔗 {t['url']}"
        lines.append(line)
    lines.append(f"\nดูทั้งหมด: https://www.tourfiremai.com/faimai")
    return "\n".join(lines)

def parse_option_index_rule_based(text: str):
    """Fast rule-based parser สำหรับ 'ตัวที่ 1', 'ตัวแรก', '1' ฯลฯ
    Returns 1-indexed int or None — ไม่ต้องรอ Claude"""
    import re as _re
    t = text.strip()
    # รูปแบบ: "ตัวที่ 1", "อันที่ 2", "ข้อที่ 3", "โปรแกรมที่ 1"
    m = _re.search(
        r'(?:ตัวที่|อันที่|ข้อที่|โปรแกรมที่|ตัวที[่]?|อันที[่]?|ข้อที[่]?)\s*([๑-๙\d]+)', t
    )
    if m:
        n = m.group(1).translate(str.maketrans('๑๒๓๔๕๖๗๘๙๐', '1234567890'))
        try:
            return int(n)
        except ValueError:
            pass
    # Thai ordinal words
    if _re.search(r'(?:ตัวแรก|อันแรก|ข้อแรก|ตัวที่หนึ่ง|เอาตัวแรก|สนใจตัวแรก|เลือกตัวแรก|อันนี้ตัวแรก)', t):
        return 1
    if _re.search(r'(?:ตัวสอง|อันสอง|ข้อสอง|ตัวที่สอง|สนใจตัวที่สอง|เอาตัวสอง|อันที่สอง)', t):
        return 2
    if _re.search(r'(?:ตัวสาม|อันสาม|ข้อสาม|ตัวที่สาม|สนใจตัวที่สาม|เอาตัวสาม|อันที่สาม)', t):
        return 3
    # "รายละเอียดตัวที่ 1", "ขอรายละเอียดตัวที่ 1"
    m2 = _re.search(r'รายละเอียด.*?(?:ตัวที่|อันที่|ข้อที่|ตัว|อัน|ข้อ)\s*([1-3๑-๓])', t)
    if m2:
        n = m2.group(1).translate(str.maketrans('๑๒๓', '123'))
        try:
            return int(n)
        except ValueError:
            pass
    # "สนใจตัวที่ 1", "เอาอันที่ 2"
    m3 = _re.search(r'(?:สนใจ|เอา|เลือก|ขอ|ต้องการ)\s*(?:ตัวที่|อันที่|ข้อที่|ตัว|อัน|ข้อ)\s*([1-3๑-๓])', t)
    if m3:
        n = m3.group(1).translate(str.maketrans('๑๒๓', '123'))
        try:
            return int(n)
        except ValueError:
            pass
    # Lone digit "1" / "2" / "3" (very short message)
    if len(t) <= 5 and _re.fullmatch(r'\s*([1-3])\s*', t):
        return int(t.strip())
    return None


def decide_action(user_message: str, history: list, last_options_count: int = 0,
                 current_search_mode: str = "normal") -> dict:
    history_text = ""
    for msg in history[-10:]:
        role = "ลูกค้า" if msg["role"] == "user" else "AI"
        history_text += f"{role}: {msg['content'][:250]}\n"

    last_opts_hint = f"\n⚠️ last_options_count={last_options_count} (จำนวนทัวร์ที่เสนอล่าสุดใน context)" if last_options_count > 0 else ""
    search_mode_hint = f"\n⚠️ current_search_mode={current_search_mode} (รักษาสถานะนี้ถ้าลูกค้าไม่เปลี่ยนเจตนา)"
    prompt = (
        f"บทสนทนาที่ผ่านมา:\n{history_text}\n"
        f"--- ข้อความล่าสุดของลูกค้า (สำคัญที่สุด): {user_message} ---\n"
        f"{last_opts_hint}"
        f"{search_mode_hint}\n\n"

        "ตอบเป็น JSON เท่านั้น (ห้ามมีข้อความอื่น):\n"
        "{\n"
        '  "action": "search" | "detail" | "detail_pdf" | "departure_filter" | "flash_sale" | "handoff" | "reply" | "continue",\n'
        '  "should_search": true | false,\n'
        '  "search_mode": "normal" | "faimai" | "any",\n'
        '  "deal_type": "normal" | "faimai" | null,\n'
        '  "missing_field_to_ask": "country" | "city" | "budget_per_person" | "month" | "pax" | null,\n'
        '  "country_id": "เลขประเทศ หรือ null",\n'
        '  "country_name": "ชื่อประเทศภาษาไทย หรือ null",\n'
        '  "city": "ชื่อเมือง/จังหวัดที่ลูกค้าถามถึง เช่น โอซาก้า โตเกียว ฮอกไกโด เฉิงตู คุนหมิง หรือ null",\n'
        '  "month": "เดือนที่ลูกค้าระบุ เช่น มิ.ย. 69 หรือ null",\n'
        '  "budget_per_person": จำนวนเงินงบต่อคน (integer) หรือ null,\n'
        '  "pax": จำนวนคน (integer) หรือ null,\n'
        '  "selected_option_index": 1 | 2 | 3 | null,\n'
        '  "departure_month": "เดือนที่ลูกค้าระบุ เช่น ก.ค. หรือ null",\n'
        '  "uses_previous_option": true | false,\n'
        '  "clear_previous_options": true | false,\n'
        '  "lead_stage": "cold" | "warm" | "hot" | "booking",\n'
        '  "reason": "เหตุผลสั้นๆ ที่เลือก action นี้"\n'
        "}\n\n"

        "=== กฎ should_search ===\n"
        "should_search=false เมื่อลูกค้าพูดกว้างๆ ยังไม่รู้ปลายทาง:\n"
        "  - ทักทาย / สนใจทัวร์ไฟไหม้ / มีโปรอะไรบ้าง / แนะนำหน่อย\n"
        "  - มีประเทศไหนบ้าง / งบนี้ไปไหนได้บ้าง (ไม่ระบุประเทศ)\n"
        "  - ทัวร์ไฟไหม้คืออะไร / ทัวร์ถูกคืออะไร\n"
        "  - คำถามทั่วไปที่ AI ตอบได้โดยไม่ต้องดึงข้อมูลเว็บ\n"
        "  ในกรณีนี้: action=reply, should_search=false\n"
        "should_search=true เมื่อ:\n"
        "  - ลูกค้าระบุประเทศ/เมืองชัดเจน (ญี่ปุ่น โอซาก้า เกาหลี เฉิงตู ฯลฯ)\n"
        "  - ลูกค้าเลือกโปรแกรม (selected_option_index มีค่า)\n"
        "  - action=flash_sale และมี intent ชัดว่าอยากเห็นรายการโปรแกรม\n"
        "  - มี country_id ที่ระบุได้ชัดเจน\n\n"
        "=== กฎ missing_field_to_ask ===\n"
        "ระบุ field ที่ควรถามต่อ (เพียง 1 อย่าง) เมื่อ should_search=false:\n"
        "  country → ยังไม่รู้ว่าอยากไปไหน\n"
        "  city → รู้ประเทศแล้ว (ญี่ปุ่น/จีน/เกาหลี/ไต้หวัน) แต่ยังไม่รู้เมือง\n"
        "  budget_per_person → รู้ประเทศแล้ว ยังไม่รู้งบ (ไม่บังคับ แต่ช่วยคัดให้)\n"
        "  month → รู้ประเทศ+งบแล้ว ยังไม่รู้เดือน\n"
        "  pax → รู้เกือบครบ ยังไม่รู้จำนวนคน\n"
        "  null → ไม่มีอะไรต้องถามเพิ่ม (หรือ should_search=true)\n\n"
        "=== กฎ search_mode ===\n"
        "search_mode=faimai เมื่อ:\n"
        "  - ข้อความมีคำว่า ไฟไหม้ โปรไฟไหม้ ลดราคา flash sale ใกล้เดินทาง โปรพิเศษ\n"
        "  - current_search_mode=faimai AND ข้อความเป็นประเทศ/เมือง เช่น ญี่ปุ่น เกาหลี โอซาก้า\n"
        "    → ยังคง search_mode=faimai (ไม่เปลี่ยนถ้าลูกค้าแค่ระบุปลายทาง)\n"
        "search_mode=normal เมื่อ:\n"
        "  - ข้อความมีคำว่า ทั่วไป ปกติ ราคาเต็ม ไม่ใช่ไฟไหม้ ดูทัวร์ทั้งหมด\n"
        "  - ลูกค้าพูดชัดว่าอยากดูทัวร์ปกติ ไม่ใช่โปรไฟไหม้\n"
        "ค่า default = current_search_mode (รักษาสถานะเดิมถ้าไม่มีสัญญาณเปลี่ยน)\n"
        "deal_type เซ็ตเหมือน search_mode (faimai หรือ normal) หรือ null ถ้าไม่ชัด\n\n"
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

        "⚠️ กฎ DEPARTURE_FILTER — ตรวจสอบก่อนทุกกฎอื่น:\n"
        "ถ้า pending_action=wait_departure_month หรือ wait_departure_date\n"
        "AND ลูกค้าพิมพ์ชื่อเดือน (ก.ค., กรกฎา, สิงหา, เดือน 7, ก.ค.69, 10 ก.ค., 2-6) ฯลฯ\n"
        "→ action=departure_filter, departure_month=เดือนที่ตรวจพบ, uses_previous_option=true\n"
        "→ ห้าม reset country/program ห้ามถามประเทศซ้ำ ใช้ selected_tour จาก context\n\n"
        "action=search: ลูกค้าต้องการดูโปรแกรมทัวร์ประเทศที่ระบุ รวมถึงการเปลี่ยนประเทศ\n"
        "action=detail: ลูกค้าขอดูรายละเอียดทัวร์ — ใช้เมื่อยังไม่มีโปรแกรมที่เลือกใน context\n"
        "action=detail_pdf: (1) ลูกค้าขอ 'รายละเอียด' ของโปรแกรมที่เลือกไว้ใน context แล้ว (2) ลูกค้าถามมัดจำ/วีซ่า/ทิป/พักเดี่ยว/เงื่อนไขยกเลิก/itinerary/โรงแรม/รวมอะไร — และมีโปรแกรมที่เลือกไว้ใน context (3) ลูกค้าถามราคาแต่ละรอบ/วันเดินทาง เช่น 'รอบไหนราคาเท่าไหร่' 'ราคาเดือน X' 'แต่ละวันราคาต่างกันไหม' — ต้องอ่าน PDF ก่อน ห้ามใช้ price_min กับทุกวัน\n"
        "action=flash_sale: ลูกค้าถามทัวร์ไฟไหม้โดยไม่ระบุประเทศ เช่น 'ทัวร์ไฟไหม้มีอะไรบ้าง' 'ทัวร์ถูกๆ' 'ไฟไหม้ราคาดี' หรือถามทัวร์จากโพสเพจโดยตรง\n"
        "action=handoff: ลูกค้าพร้อมจอง/สนใจจอง/ขอคุยเซลล์/ขอคุยเจ้าหน้าที่/เช็กที่นั่ง/ขอราคา final/ขอส่วนลด/ยกเลิก/ส่งรูปจากโพสเพจ\n"
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
            max_tokens=350,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        m = re.search(r"\{.*?\}", raw, re.DOTALL)
        if m:
            data = json.loads(m.group())
            data["action"] = data.get("action", "reply")
            data["should_search"] = bool(data.get("should_search", True))
            # search_mode — default to current_search_mode if classifier returns default
            sm = data.get("search_mode", current_search_mode)
            data["search_mode"] = sm if sm in ("normal", "faimai", "any") else current_search_mode
            dt = data.get("deal_type", None)
            data["deal_type"] = dt if dt in ("normal", "faimai") else None
            mfta = data.get("missing_field_to_ask", None)
            data["missing_field_to_ask"] = mfta if mfta and mfta != "null" else None
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

    return {"action": "reply", "should_search": False, "missing_field_to_ask": "country",
            "search_mode": current_search_mode, "deal_type": None,
            "country_id": None, "country_name": None, "city": None,
            "month": None, "budget_per_person": None, "pax": None,
            "selected_option_index": None, "uses_previous_option": False,
            "clear_previous_options": False, "lead_stage": "cold", "reason": ""}


# ─── AI — Call 2: Generate Response ──────────────────────────────────────────
def generate_response(user_message: str, history: list, tour_data: str = "",
                      is_handoff: bool = False, ctx: dict = None,
                      action: str = "reply",
                      missing_field_to_ask: str = None) -> str:
    messages = []
    for msg in history[-10:]:
        messages.append({"role": msg["role"], "content": msg["content"]})

    if tour_data:
        if action == "flash_sale":
            # faimai context — ห้ามล็อกงบ ราคาเหล่านี้คือ special แล้ว
            faimai_has_result = "ขณะนี้ยังไม่มี" not in tour_data and "ไม่ได้ตอนนี้" not in tour_data
            no_result_hint = ""
            if not faimai_has_result:
                no_result_hint = (
                    "\n[ไม่พบทัวร์ไฟไหม้ตรงกับที่ค้นหา: บอกลูกค้าตรงๆ ว่าช่วงนี้ยังไม่มีโปรแกรมไฟไหม้สำหรับประเทศนั้น "
                    "แล้วแนะนำให้ดูทัวร์ปกติหรือให้ติดตามโปรแกรมใหม่ที่ /faimai]"
                )
            user_content = (
                f"{user_message}\n\n"
                "--- ทัวร์ไฟไหม้จาก tourfiremai.com/faimai (ราคาพิเศษอยู่แล้ว อย่าล็อกงบ) ---\n"
                f"{tour_data[:4000]}\n"
                "---\n"
                "คำแนะนำ: เสนอ 2-3 โปรแกรมที่น่าสนใจจากข้อมูลด้านบน ถ้าลูกค้าระบุประเทศแต่ไม่มีในหน้าไฟไหม้ "
                "ให้บอกตรงๆ และแนะนำประเทศอื่นที่มีในหน้าไฟไหม้แทน "
                "ห้ามเปรียบเทียบกับราคาปกติหรือแนะนำให้เพิ่มงบ"
                + no_result_hint
            )
        elif ctx and ctx.get("search_mode") == "faimai":
            # ── Faimai DB results (search_mode=faimai via Supabase) ──────
            faimai_has_result = "ขณะนี้ยังไม่มี" not in tour_data and len(tour_data.strip()) > 20
            no_result_hint = ""
            if not faimai_has_result:
                no_result_hint = (
                    "\n[ไม่พบทัวร์ไฟไหม้ตรงกับที่ค้นหา: บอกลูกค้าตรงๆ ว่าช่วงนี้ยังไม่มีโปรไฟไหม้สำหรับประเทศนั้น "
                    "แล้วแนะนำให้ดูทัวร์ปกติหรือให้ติดตามโปรแกรมใหม่]"
                )
            user_content = (
                f"{user_message}\n\n"
                "--- โปรไฟไหม้จาก tourfiremai.com (ราคาพิเศษ ลดจากปกติ) ---\n"
                f"{tour_data[:4000]}\n"
                "---\n"
                "คำแนะนำ: เปิดด้วย 'มีค่ะ โปรไฟไหม้...' เสนอ 2-3 โปรแกรมที่น่าสนใจ "
                "เน้นส่วนลด/ความคุ้มค่า ห้ามเปรียบเทียบกับราคาปกติหรือแนะนำให้เพิ่มงบ "
                "ห้ามใช้คำว่าน้องแอดมิน"
                + no_result_hint
                + (f"\n[งบลูกค้า {ctx['budget_per_person']} บาท: คัดเฉพาะที่ราคาไม่เกินงบก่อน ถ้าไม่มีให้บอกตรงๆ]" if ctx.get("budget_per_person") else "")
            )
        else:
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
        # qualification mode — ไม่มีข้อมูลทัวร์ ให้ตอบแบบ conversational
        _field_hints = {
            "country": "ถามว่าอยากไปประเทศหรือภูมิภาคไหน",
            "city": "ถามว่าสนใจเมือง/เส้นทางไหนในประเทศนั้น",
            "budget_per_person": "ถามงบประมาณต่อคน (ตอบข้อมูลภาพรวมก่อน แล้วค่อยถาม)",
            "month": "ถามช่วงเดือน/วันที่สะดวกเดินทาง",
            "pax": "ถามจำนวนผู้เดินทาง",
        }
        _ask_hint = _field_hints.get(missing_field_to_ask, "") if missing_field_to_ask else ""
        _ask_instruction = (
            f"\n[QUALIFICATION MODE: ตอบแบบ AI Travel Assistant ที่ฉลาด"
            f" ให้ข้อมูลทั่วไปที่เป็นประโยชน์ก่อน แล้ว{_ask_hint}"
            " ห้ามเสนอโปรแกรมทัวร์ในข้อความนี้ ห้ามใช้คำว่าน้องแอดมิน"
            " ตอบสั้นอ่านง่าย ถามทีละ 1 คำถาม]"
        ) if _ask_hint else (
            "\n[AI Travel Assistant: ตอบด้วยความรู้ทั่วไปเกี่ยวกับทัวร์/การเดินทาง"
            " ถ้าลูกค้าเปลี่ยนประเทศอย่านำทัวร์เก่ามาแสดงซ้ำ ถามต่ออย่างเป็นธรรมชาติ]"
        )
        user_content = f"{user_message}{_ask_instruction}"

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
# Keywords ที่บ่งบอกว่ารูปที่ส่งมาคือสลิปการโอนเงิน
_PAYMENT_IMAGE_KEYWORDS = [
    "โอนแล้ว", "จ่ายแล้ว", "ชำระแล้ว", "สลิป", "แนบสลิป",
    "หลักฐานการโอน", "มัดจำแล้ว", "โอนมัดจำ", "โอนค่าทัวร์",
    "ส่งสลิป", "สลิปแล้ว", "โอนเงินแล้ว", "จ่ายเงินแล้ว",
    "ชำระเงินแล้ว", "โอนมาแล้ว", "จ่ายมาแล้ว",
]

def _build_tour_context_summary(ctx: dict) -> list:
    """Helper — build tour context lines for notifications"""
    lines = []
    if ctx.get("selected_tour_name"):
        lines.append(f"🏷 ทัวร์: {ctx['selected_tour_name']}")
        if ctx.get("selected_tour_code"):
            lines.append(f"   รหัสทัวร์: {ctx['selected_tour_code']}")
        if ctx.get("selected_tour_web_code"):
            lines.append(f"   รหัสเว็บ: {ctx['selected_tour_web_code']}")
        if ctx.get("selected_tour_airline"):
            lines.append(f"   สายการบิน: {ctx['selected_tour_airline']}")
    dest = " / ".join(filter(None, [ctx.get("country"), ctx.get("city_hint")]))
    if dest:
        lines.append(f"📍 ปลายทาง: {dest}")
    if ctx.get("pax"):
        lines.append(f"👥 จำนวน: {ctx['pax']} ท่าน")
    if ctx.get("travel_date"):
        lines.append(f"📅 วันเดินทาง: {ctx['travel_date']}")
    return lines


def generate_case_id(sender_id: str) -> str:
    """สร้าง Case ID รูปแบบ TF-HHMM-XXXX (เวลา Asia/Bangkok = UTC+7)
    ตัวอย่าง: TF-1432-A7F9
    """
    now_bkk = datetime.utcnow() + timedelta(hours=7)
    hhmm = now_bkk.strftime("%H%M")
    xxxx = sender_id[-4:].upper()
    return f"TF-{hhmm}-{xxxx}"


def get_or_create_case_id(sender_id: str, ctx: dict) -> str:
    """คืน case_id ที่มีอยู่แล้ว หรือสร้างใหม่ถ้ายังไม่มี"""
    if ctx.get("case_id"):
        return ctx["case_id"]
    case_id = generate_case_id(sender_id)
    ctx["case_id"] = case_id
    return case_id


def pause_bot(sender_id: str, ctx: dict, reason: str, hours: int) -> None:
    """Set bot_paused_until ใน ctx dict (ยังไม่ save — ให้ caller save หลังจากนี้)
    reason: image_handoff | payment_pending_review | handoff_requested | booking
    """
    until = (datetime.utcnow() + timedelta(hours=hours)).isoformat() + "Z"
    ctx["bot_paused_until"] = until
    ctx["bot_pause_reason"] = reason
    ctx["human_takeover"]   = True
    ctx["case_status"]      = "waiting_team"
    logger.info(f"🛑 Bot paused: psid=...{sender_id[-6:]}, reason={reason}, hours={hours}, until={until}")


# ─── Go-Live Guard Helpers ────────────────────────────────────────────────────
def is_legacy_psid(sender_id: str) -> bool:
    """ตรวจว่า PSID มี record ใน Supabase ก่อนเวลา go-live หรือไม่
    คืน True = legacy conversation (bot ห้ามตอบ)
    """
    go_live = _go_live_dt()
    if not go_live or not BOT_NEW_CONTEXT_ONLY:
        return False
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        go_live_iso = go_live.strftime("%Y-%m-%dT%H:%M:%S")
        # ตรวจ leads table — created_at ก่อน go-live
        url = f"{SUPABASE_URL}/rest/v1/leads"
        params = {
            "psid": f"eq.{sender_id}",
            "created_at": f"lt.{go_live_iso}",
            "select": "psid",
            "limit": "1",
        }
        resp = requests.get(url, params=params, headers=_sb_headers(), timeout=5)
        if resp.ok and resp.json():
            logger.info(f"🔎 Legacy PSID detected (leads): ...{sender_id[-6:]}")
            return True
        # ตรวจ ai_chat_events table ด้วย
        url2 = f"{SUPABASE_URL}/rest/v1/ai_chat_events"
        params2 = {
            "psid": f"eq.{sender_id}",
            "created_at": f"lt.{go_live_iso}",
            "select": "psid",
            "limit": "1",
        }
        resp2 = requests.get(url2, params=params2, headers=_sb_headers(), timeout=5)
        if resp2.ok and resp2.json():
            logger.info(f"🔎 Legacy PSID detected (chat_events): ...{sender_id[-6:]}")
            return True
    except Exception as e:
        logger.warning(f"is_legacy_psid error: {e}")
    return False


def handle_legacy_conversation(sender_id: str, ctx: dict, text: str) -> None:
    """Block bot, pause 24h, notify team, log event"""
    case_id = get_or_create_case_id(sender_id, ctx)
    ctx["legacy_conversation"] = True
    ctx["bot_allowed"]         = False
    pause_bot(sender_id, ctx, "legacy_conversation", hours=24)
    save_context(sender_id, ctx)

    display_name = ctx.get("customer_name") or f"PSID ...{sender_id[-6:]}"
    notify_line(
        f"🔕 Legacy conversation — Bot ไม่ตอบเพื่อกันแทรกแอดมิน\n"
        f"🆔 Case: {case_id}\n"
        f"👤 {display_name}\n"
        f"💬 ข้อความล่าสุด: {text[:200]}\n"
        f"📋 เหตุผล: มีประวัติก่อนเริ่ม Go Live\n"
        f"✅ Action: ทีมงานเปิด Messenger และตอบเอง"
    )
    log_chat_event(sender_id, "legacy_conversation_handoff", ctx=ctx,
                   message=text, needs_review=True, review_reason="legacy_conversation")
    logger.info(f"🔕 Legacy handoff complete: ...{sender_id[-6:]}")


def init_new_session(sender_id: str, ctx: dict) -> None:
    """ตั้ง session สำหรับ new customer หลัง go-live"""
    now_iso = (datetime.utcnow() + timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%S+07:00")
    ctx["bot_session_started_at"] = now_iso
    ctx["bot_memory_started_at"]  = now_iso
    ctx["bot_allowed"]            = True
    ctx["legacy_conversation"]    = False
    logger.info(f"✅ New session init: ...{sender_id[-6:]} at {now_iso}")


def process_image_handoff(sender_id: str, image_urls: list, accompanying_text: str = ""):
    """รูปภาพทั่วไป — handoff ให้ทีมงาน ห้าม Bot วิเคราะห์ภาพเอง"""
    ctx = get_context(sender_id)
    display_name = ctx.get("customer_name") or f"PSID ...{sender_id[-6:]}"

    # Elevate stage if in active conversation
    current_stage = ctx.get("_lead_stage") or "cold"
    new_stage = "warm" if current_stage in ("cold",) else current_stage

    case_id = get_or_create_case_id(sender_id, ctx)
    ctx["_lead_stage"] = new_stage
    ctx["_lead_status"] = "waiting_team"
    ctx["_lead_needs_review"] = True
    ctx["_lead_review_reason"] = "image_handoff"
    ctx["handoff_requested"] = True
    pause_bot(sender_id, ctx, "image_handoff", hours=2)
    save_context(sender_id, ctx)

    # Build LINE notification
    parts = [f"📷 ลูกค้าส่งรูปให้เช็กโปรแกรม", f"🆔 Case: {case_id}", f"👤 {display_name}"]
    if ctx.get("phone"):
        parts.append(f"📞 {ctx['phone']}")
    if accompanying_text:
        parts.append(f"💬 ข้อความ: {accompanying_text[:150]}")
    parts.append("📋 บริบทล่าสุด:")
    parts.extend(_build_tour_context_summary(ctx))
    parts.append(f"   stage: {new_stage}")
    if ctx.get("search_mode"):
        parts.append(f"   search_mode: {ctx['search_mode']}")
    parts.append("✅ Action: เปิด Messenger เพื่อดูรูปและตอบลูกค้า")
    parts.append("🔗 Dashboard: เปิด Lead Dashboard เพื่อ Pause/Resume Bot")
    notify_line("\n".join(parts))

    # Log & save
    log_chat_event(sender_id, "image_handoff", ctx=ctx,
                   message=accompanying_text or "[รูปภาพ]",
                   needs_review=True, review_reason="image_handoff")
    save_lead_supabase(sender_id, ctx, new_stage, "ส่งรูปให้เช็กโปรแกรม")

    # Silent handoff — ไม่ตอบลูกค้า ไม่บันทึก assistant history
    # ให้พนักงานดูรูปและตอบเองผ่าน LINE notification ด้านบน
    logger.info(f"📷 image_handoff (silent) for {sender_id}: stage={new_stage}")


def process_payment_pending_review(sender_id: str, image_urls: list, accompanying_text: str = ""):
    """รูป + keyword โอน/สลิป — รอทีมตรวจสอบ ห้าม auto paid"""
    ctx = get_context(sender_id)
    display_name = ctx.get("customer_name") or f"PSID ...{sender_id[-6:]}"

    case_id = get_or_create_case_id(sender_id, ctx)
    ctx["_lead_stage"] = "booking"
    ctx["_lead_status"] = "waiting_team"
    ctx["_lead_needs_review"] = True
    ctx["_lead_review_reason"] = "payment_pending_review"
    ctx["payment_received"] = False   # ยังไม่ verified — ห้าม auto paid
    ctx["handoff_requested"] = True
    pause_bot(sender_id, ctx, "payment_pending_review", hours=6)
    save_context(sender_id, ctx)

    # Build LINE notification
    parts = [f"💳 ลูกค้าอาจส่งสลิป/หลักฐานการโอน", f"🆔 Case: {case_id}", f"👤 {display_name}"]
    if ctx.get("phone"):
        parts.append(f"📞 {ctx['phone']}")
    if accompanying_text:
        parts.append(f"💬 ข้อความ: {accompanying_text[:150]}")
    parts.append("📋 บริบทล่าสุด:")
    parts.extend(_build_tour_context_summary(ctx))
    parts.append("   stage: booking")
    parts.append("✅ Action: เปิด Messenger เพื่อตรวจสลิป และยืนยันก่อนเปลี่ยนสถานะเป็น paid")
    parts.append("🔗 Dashboard: เปิด Lead Dashboard เพื่อ Pause/Resume Bot")
    notify_line("\n".join(parts))

    # Log & save
    log_chat_event(sender_id, "payment_pending_review", ctx=ctx,
                   message=accompanying_text or "[รูปสลิป]",
                   needs_review=True, review_reason="payment_pending_review")
    save_lead_supabase(sender_id, ctx, "booking", "ส่งรูปสลิป — รอตรวจสอบ")

    # Silent handoff — ไม่ตอบลูกค้า ไม่บันทึก assistant history
    # ให้พนักงานตรวจสลิปและยืนยันเองผ่าน LINE notification ด้านบน
    logger.info(f"💳 payment_pending_review (silent) for {sender_id}")


def process_payment_slip(sender_id: str, image_urls: list = None):
    """Text-only keyword โอน/สลิป (ไม่มีรูป) — รอทีมตรวจสอบ ห้าม auto paid"""
    ctx = get_context(sender_id)
    display_name = ctx.get("customer_name") or f"PSID ...{sender_id[-6:]}"

    case_id = get_or_create_case_id(sender_id, ctx)
    ctx["_lead_stage"] = "booking"
    ctx["_lead_status"] = "waiting_team"
    ctx["_lead_needs_review"] = True
    ctx["_lead_review_reason"] = "payment_pending_review"
    ctx["payment_received"] = False   # ห้าม auto paid — ต้องตรวจสอบก่อน
    ctx["handoff_requested"] = True
    pause_bot(sender_id, ctx, "payment_pending_review", hours=6)
    save_context(sender_id, ctx)

    summary_parts = [f"💳 แจ้งโอนเงิน (ข้อความ)", f"🆔 Case: {case_id}", f"👤 {display_name}"]
    summary_parts.extend(_build_tour_context_summary(ctx))
    if image_urls:
        summary_parts.append(f"🖼 รูปแนบ: {image_urls[0]}")
    summary_parts.append("⚠️ รอตรวจสอบ — ห้าม auto paid")
    summary_parts.append("🔗 Dashboard: เปิด Lead Dashboard เพื่อ Pause/Resume Bot")
    notify_line("\n".join(summary_parts))

    log_chat_event(sender_id, "payment_pending_review", ctx=ctx,
                   message="[แจ้งโอนผ่านข้อความ]",
                   needs_review=True, review_reason="payment_pending_review")
    save_lead_supabase(sender_id, ctx, "booking", "แจ้งโอนเงิน — รอตรวจสอบ")

    reply = (
        "ได้รับแล้วค่ะ ขอบคุณนะคะ 😊 "
        "เดี๋ยวทีมงานตรวจสอบและยืนยันการชำระเงินกลับให้นะคะ"
    )
    send_message(sender_id, reply)
    save_to_history(sender_id, "assistant", reply)
    logger.info(f"✅ Payment slip (text) processed for {sender_id}")


# ─── Core message processing ──────────────────────────────────────────────────
def process_message(sender_id: str, text: str):
    """Main logic — รันใน background thread"""
    logger.info(f"Processing [{sender_id}]: {text[:80]}")
    try:
        history = list(get_history(sender_id))
        ctx = get_context(sender_id)

        # ── Go-Live Guard: Legacy Conversation Detection ───────────────────────
        if BOT_NEW_CONTEXT_ONLY and _go_live_dt():
            # ถ้า ctx บอกว่า legacy แล้ว → block ทันที (ไม่ต้อง query Supabase ซ้ำ)
            if ctx.get("legacy_conversation") and ctx.get("bot_allowed") is False:
                logger.info(f"🔕 Legacy (cached): ...{sender_id[-6:]} — skip AI")
                # Re-use pause block to check if still within pause window
                paused_until_str = ctx.get("bot_paused_until")
                if paused_until_str:
                    try:
                        paused_until = datetime.fromisoformat(paused_until_str.rstrip("Z"))
                        if datetime.utcnow() < paused_until:
                            remaining_min = int((paused_until - datetime.utcnow()).total_seconds() / 60)
                            display_name = ctx.get("customer_name") or f"PSID ...{sender_id[-6:]}"
                            case_id = ctx.get("case_id", "N/A")
                            notify_line(
                                f"💬 ลูกค้าพิมพ์เพิ่มระหว่าง Human Takeover\n"
                                f"🆔 Case: {case_id}\n"
                                f"👤 {display_name}\n"
                                f"💬 {text[:200]}\n"
                                f"⏳ Bot หยุดอีก {remaining_min} นาที (สาเหตุ: legacy_conversation)\n"
                                f"✅ กรุณาตอบลูกค้าเองผ่าน Messenger"
                            )
                            log_chat_event(sender_id, "bot_paused_message", ctx=ctx,
                                           message=text, needs_review=True,
                                           review_reason="legacy_conversation")
                            return
                    except Exception:
                        pass
                # pause หมดอายุแล้วแต่ยังเป็น legacy → re-pause 24h และ notify
                handle_legacy_conversation(sender_id, ctx, text)
                return
            # ถ้ายังไม่รู้ว่า legacy → ไปถามDatabase
            if ctx.get("bot_allowed") is None:
                if is_legacy_psid(sender_id):
                    handle_legacy_conversation(sender_id, ctx, text)
                    return
                else:
                    # New customer หลัง go-live
                    init_new_session(sender_id, ctx)
                    save_context(sender_id, ctx)

        # ── Bot Pause Check (Human Takeover) ──────────────────────────────────
        paused_until_str = ctx.get("bot_paused_until")
        if paused_until_str:
            try:
                paused_until = datetime.fromisoformat(paused_until_str.rstrip("Z"))
                if datetime.utcnow() < paused_until:
                    remaining_min = int((paused_until - datetime.utcnow()).total_seconds() / 60)
                    reason = ctx.get("bot_pause_reason", "human_takeover")
                    logger.info(f"🛑 Bot paused [{reason}] psid=...{sender_id[-6:]}, {remaining_min}m left — skip AI")
                    display_name = ctx.get("customer_name") or f"PSID ...{sender_id[-6:]}"
                    case_id = ctx.get("case_id", "N/A")
                    notify_line(
                        f"💬 ลูกค้าพิมพ์เพิ่มระหว่าง Human Takeover\n"
                        f"🆔 Case: {case_id}\n"
                        f"👤 {display_name}\n"
                        f"💬 {text[:200]}\n"
                        f"⏳ Bot หยุดอีก {remaining_min} นาที (สาเหตุ: {reason})\n"
                        f"✅ กรุณาตอบลูกค้าเองผ่าน Messenger"
                    )
                    log_chat_event(sender_id, "bot_paused_message", ctx=ctx,
                                   message=text, needs_review=True, review_reason=reason)
                    return
            except Exception as _pe:
                logger.warning(f"bot_paused_until parse error for {sender_id}: {_pe}")
                # ถ้า parse ไม่ได้ → ปล่อยผ่าน ไม่หยุด bot

        # ── Auto-fetch FB profile name ถ้ายังไม่มีชื่อ ───────────────────────
        if not ctx.get("customer_name"):
            fb_profile = fetch_fb_profile(sender_id)
            if fb_profile.get("name"):
                ctx["customer_name"] = fb_profile["name"]
                save_context(sender_id, ctx)
                logger.info(f"👤 Auto-filled customer_name: {fb_profile['name']}")

        # ── Payment slip detection via text keywords ──────────────────────────
        text_lower = text.lower()
        if any(kw in text_lower for kw in _PAYMENT_KEYWORDS):
            logger.info(f"💳 Payment keyword detected for {sender_id}")
            save_to_history(sender_id, "user", text)
            process_payment_slip(sender_id)
            return

        _last_opts_count = len(ctx.get("last_options", []))
        if _last_opts_count > 0:
            logger.info(f"[MEMORY_LOAD] psid=...{sender_id[-6:]} last_options_count={_last_opts_count}")
        # ── Rule-based option selection (fast, before Claude call) ─────
        _rule_option_idx = parse_option_index_rule_based(text) if _last_opts_count > 0 else None
        if _rule_option_idx:
            logger.info(f"[OPTION_SELECT] rule-based detected: index={_rule_option_idx} text={text[:40]!r}")
        action_data = decide_action(text, history, last_options_count=_last_opts_count)
        action               = action_data.get("action", "reply")
        country_id           = action_data.get("country_id")
        selected_option_idx  = action_data.get("selected_option_index")
        uses_previous        = action_data.get("uses_previous_option", False)
        # Rule-based override: ถ้า Claude ไม่ detect แต่ rule-based ตรวจเจอ
        if _rule_option_idx and not selected_option_idx:
            selected_option_idx = _rule_option_idx
            uses_previous = True
            logger.info(f"[OPTION_SELECT] rule-based override applied: index={_rule_option_idx}")
        clear_prev_options   = action_data.get("clear_previous_options", False)
        lead_stage           = action_data.get("lead_stage", "cold")
        should_search        = action_data.get("should_search", True)
        missing_field_to_ask = action_data.get("missing_field_to_ask", None)
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
                ctx["selected_tour"]           = selected
                ctx["selected_tour_name"]      = selected.get("name", "")
                ctx["selected_tour_url"]       = selected.get("url", selected.get("link", ""))
                ctx["selected_tour_code"]      = selected.get("tour_code_real", "") or selected.get("tour_code", "") or ""
                ctx["selected_tour_web_code"]  = selected.get("web_code", "") or selected.get("tour_code", "") or ""
                ctx["selected_tour_airline"]   = selected.get("airline", "") or ""
                logger.info(f"✅ Resolved option #{selected_option_idx}: {ctx['selected_tour_name']} [{ctx['selected_tour_code']}]")
                logger.info(f"[OPTION_SELECT] psid=...{sender_id[-6:]} selected_index={selected_option_idx} web_code={ctx.get('selected_tour_web_code','')} tour_code={ctx.get('selected_tour_code','')}")
                # Elevate lead stage when option selected
                if lead_stage not in ("hot", "booking", "paid"):
                    lead_stage = "hot"
                    ctx["_lead_stage"] = "hot"

        # ── clear_previous_options: ลูกค้าเปลี่ยนประเทศ ──────────────────
        if clear_prev_options:
            ctx["last_options"] = []
            ctx["selected_tour"] = None
            ctx["selected_tour_name"] = None
            ctx["selected_tour_url"] = None
            ctx["city_hint"] = None
            logger.info(f"🔄 clear_previous_options triggered for {sender_id}")
        save_context(sender_id, ctx)

        # ── Fallback: ลูกค้าเลือก option แต่ last_options หายจาก Redis ──
        if uses_previous and selected_option_idx and not ctx.get("last_options"):
            _retry_country = country_id or ctx.get("country_id")
            _retry_city    = city_hint or ctx.get("city_hint")
            if _retry_country:
                logger.info(f"[MEMORY_FALLBACK] last_options empty, re-searching country_id={_retry_country} city={_retry_city}")
                action = "search"
                country_id = _retry_country
                city_hint  = _retry_city
                uses_previous = False
                selected_option_idx = None
                ctx["_fallback_reason"] = "options_expired"
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

        # ── Flash sale context override ────────────────────────────────────
        # ถ้ายังอยู่ใน flash_sale context และ action=search/reply → ดึง faimai แทน
        if ctx.get("pending_action") == "flash_sale" and action in ("search", "reply"):
            if action == "search" and country_id:
                # ลูกค้าระบุประเทศใหม่ใน flash_sale context → กรองใน faimai
                action = "flash_sale"
                logger.info(f"Flash sale context: redirecting search({country_id}) to faimai")
            elif action == "reply" and not country_id:
                # ยังถามอยู่ใน topic เดิม → ดึง faimai ต่อ
                action = "flash_sale"
                logger.info("Flash sale context: maintaining flash_sale for reply")

        # ── should_search gate — ถ้า false ไม่ดึงข้อมูลเว็บ ตอบแบบ conversational ──
        if not should_search and action in ("search", "flash_sale"):
            logger.info(f"should_search=False: downgrade {action}→reply (ask: {missing_field_to_ask})")
            action = "reply"

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
                        ctx["selected_tour_url"]       = t0.get("url", t0.get("link", ""))
                        ctx["selected_tour_code"]      = t0.get("tour_code_real", "") or t0.get("tour_code", "") or ""
                        ctx["selected_tour_web_code"]  = t0.get("web_code", "") or t0.get("tour_code", "") or ""
                        ctx["selected_tour_airline"]   = t0.get("airline", "") or ""
                        logger.info(f"🎯 Auto-selected single tour: {ctx['selected_tour_name']}")
                    ctx["pending_action"] = "wait_user"
                    ctx["last_bot_message_type"] = "tour_options"
                    save_context(sender_id, ctx)
                    logger.info(f"last_options updated immediately: {len(tour_meta)} tours")
                    logger.info(f"[MEMORY_SAVE] psid=...{sender_id[-6:]} last_options_count={len(tour_meta)} search_mode={ctx.get('search_mode','normal')}")
            except Exception as e:
                logger.error(f"fetch_tours error: {e}")
                tour_data, tour_meta = "", []
                ctx["_lead_needs_review"] = True
                ctx["_lead_review_reason"] = "no_tour_found"
                # PATCH 7 — notify team when hot/booking lead can't find tours
                _no_tour_stage = ctx.get("lead_stage", "cold")
                if _no_tour_stage in ("hot", "booking"):
                    _case_id = get_or_create_case_id(sender_id, ctx)
                    _display = ctx.get("customer_name") or f"PSID ...{sender_id[-6:]}"
                    _dest = ctx.get("destination") or ctx.get("country_name") or "ไม่ระบุ"
                    notify_line(
                        f"⚠️ ดึงทัวร์ไม่ได้ — Lead [{_no_tour_stage.upper()}]\n"
                        f"🆔 Case: {_case_id}\n"
                        f"👤 {_display}\n"
                        f"🌍 ปลายทาง: {_dest}\n"
                        f"❌ Error: {str(e)[:120]}\n"
                        f"✅ กรุณาตอบลูกค้าด้วยตนเอง"
                    )
                    save_context(sender_id, ctx)

        # Fetch PDF info
        if action == "detail_pdf":
            program_url = (
                ctx.get("selected_tour_url")
                or extract_program_url_from_history(history)
            )
            if program_url:
                logger.info(f"Fetching full detail for: {program_url}")
                try:
                    # Step 1: HTML fee info (ทิป/มัดจำ/วีซ่า/พักเดี่ยว)
                    html_detail = fetch_tour_detail_full(program_url)
                    # Step 2: Structured departure table (parse into monthly groups)
                    dep_structured = fetch_departure_structured(program_url)
                    dep_str = format_departure_for_chat(dep_structured)
                    # Step 3: PDF (เงื่อนไขยกเลิก/itinerary)
                    pdf_detail = fetch_pdf_info(program_url)

                    # Save departure context to Redis BEFORE generate_response
                    if dep_structured.get("rows"):
                        ctx["departure_options_by_month"] = dep_structured["by_month"]
                        ctx["available_departure_months"] = dep_structured["month_order"]
                        if len(dep_structured["rows"]) > 5:
                            ctx["pending_action"] = "wait_departure_month"
                            ctx["last_bot_message_type"] = "tour_detail_summary"
                        save_context(sender_id, ctx)
                        logger.info(
                            f"[DEPARTURE] rows={len(dep_structured['rows'])} "
                            f"months={dep_structured['month_order']}"
                        )

                    parts = []
                    if html_detail:
                        parts.append(html_detail)
                    if dep_str:
                        parts.append(dep_str)
                    if pdf_detail:
                        parts.append(pdf_detail)
                    tour_data = "\n\n".join(parts) if parts else ""
                except Exception as e:
                    logger.error(f"fetch detail error: {e}")
                    tour_data = ""
            else:
                action = "reply"

        # ── Departure month filter ────────────────────────────────────────────
        if action == "departure_filter":
            selected_month = action_data.get("departure_month") or detect_departure_month_from_text(text)
            dep_by_month = ctx.get("departure_options_by_month") or {}
            sel_tour = ctx.get("selected_tour") or {}
            tour_name = ctx.get("selected_tour_name") or sel_tour.get("name", "")
            web_code  = ctx.get("selected_tour_web_code") or sel_tour.get("web_code", "")
            tip_summary = ""
            # Try to get fee from DB or ctx
            last_opts = ctx.get("last_options") or []
            for opt in last_opts:
                if opt.get("web_code") == web_code and opt.get("tip_fee"):
                    tip_summary = f"\nทิปไกด์: {opt['tip_fee']:,} บาท/ท่าน"
                    if opt.get("visa_status"):
                        tip_summary += f" | วีซ่า: {opt['visa_status']}"
                    break

            if selected_month and dep_by_month:
                # Filter matching month (fuzzy: also check nearby)
                month_rows = dep_by_month.get(selected_month, [])
                if not month_rows:
                    # Try case-insensitive
                    for k, v in dep_by_month.items():
                        if selected_month.rstrip(".") in k or k.rstrip(".") in selected_month:
                            month_rows = v
                            selected_month = k
                            break

                if month_rows:
                    lines = [f"รอบเดือน {selected_month} ของโปรแกรม {web_code or tour_name} ค่ะ"]
                    for r in month_rows:
                        line = f"- {r['date']}"
                        if r.get("adult"):
                            line += f"  ผู้ใหญ่ {r['adult']:,}"
                        if r.get("child_no_bed"):
                            line += f" / เด็กไม่มีเตียง {r['child_no_bed']:,}"
                        lines.append(line)
                    if tip_summary:
                        lines.append(tip_summary)
                    tour_data = "\n".join(lines)
                    tour_data += (
                        "\n[INSTRUCTION_FOR_BOT] แสดงรายการรอบด้านบนแบบ bullet list ห้ามใช้ markdown table "
                        "แล้วถามว่าสนใจรอบไหนเป็นพิเศษ และถามจำนวนผู้เดินทางถ้ายังไม่ทราบ"
                    )
                    # Update pending_action
                    ctx["pending_action"] = "wait_departure_date"
                    ctx["last_selected_departure_month"] = selected_month
                    save_context(sender_id, ctx)
                    logger.info(f"[DEPARTURE_FILTER] month={selected_month} rows={len(month_rows)}")
                else:
                    available = ", ".join(ctx.get("available_departure_months") or list(dep_by_month.keys()))
                    tour_data = (
                        f"[INSTRUCTION_FOR_BOT] ไม่พบรอบเดือน {selected_month} "
                        f"มีเดือนที่ว่าง: {available} "
                        f"ให้บอกลูกค้าว่าไม่มีรอบเดือนนั้น และถามว่าสนใจเดือนอื่นไหม"
                    )
            else:
                available = ", ".join(ctx.get("available_departure_months") or list(dep_by_month.keys()))
                tour_data = (
                    f"[INSTRUCTION_FOR_BOT] ลูกค้าพิมพ์ว่า '{text}' ยังไม่ชัดว่าต้องการเดือนไหน "
                    f"มีรอบ: {available} ให้ถามซ้ำว่าสนใจเดือนไหนจากรายการนี้"
                )

        # Notify admin
        if action == "flash_sale":
            # ดึงทัวร์ไฟไหม้ — ถ้ามี country_id ใช้ DB (structured, มี tour_meta)
            faimai_country = (
                action_data.get("country_name") or
                COUNTRY_MAP.get(action_data.get("country_id") or "", "") or
                ctx.get("country_name") or ""
            )
            _flash_country_id = country_id or ctx.get("country_id")
            if _flash_country_id:
                # DB path — ได้ structured tour_meta → save last_options
                try:
                    _budget_max = None
                    if ctx.get("budget_per_person"):
                        try:
                            _budget_max = int(str(ctx["budget_per_person"]).replace(",","").replace(" ",""))
                        except Exception:
                            pass
                    _flash_result = fetch_tours(
                        _flash_country_id,
                        city_hint=city_hint or ctx.get("city_hint"),
                        budget_max=_budget_max,
                        search_mode="faimai"
                    )
                    if isinstance(_flash_result, tuple):
                        tour_data, _flash_meta = _flash_result
                    else:
                        tour_data, _flash_meta = _flash_result, []
                    if _flash_meta:
                        ctx["last_options"] = _flash_meta
                        ctx["pending_action"] = "wait_user"
                        ctx["last_bot_message_type"] = "tour_options"
                        save_context(sender_id, ctx)
                        logger.info(f"[MEMORY_SAVE] psid=...{sender_id[-6:]} last_options_count={len(_flash_meta)} search_mode=faimai")
                    logger.info(f"flash_sale DB: country_id={_flash_country_id}, meta_count={len(_flash_meta)}, fetched {len(tour_data)} chars")
                except Exception as _fe:
                    logger.error(f"flash_sale fetch_tours error: {_fe}")
                    tour_data = fetch_faimai_tours(country_filter=faimai_country if faimai_country else None)
                    logger.info(f"flash_sale fallback to web: country_filter={faimai_country!r}")
            else:
                # ไม่มี country_id → ใช้หน้า /faimai (text only, ไม่ได้ tour_meta)
                tour_data = fetch_faimai_tours(country_filter=faimai_country if faimai_country else None)
                logger.info(f"flash_sale web: country_filter={faimai_country!r}, fetched {len(tour_data)} chars")
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
                if ctx.get("selected_tour_code"):
                    ctx_summary += f"\nรหัสทัวร์จริง: {ctx['selected_tour_code']}"
                if ctx.get("selected_tour_web_code"):
                    ctx_summary += f"\nรหัสเว็บ: {ctx['selected_tour_web_code']}"
                if ctx.get("selected_tour_airline"):
                    ctx_summary += f"\nสายการบิน: {ctx['selected_tour_airline']}"
                if ctx.get("selected_tour_url"):
                    ctx_summary += f"\nลิงก์: {ctx['selected_tour_url']}"
            elif ctx.get("destination"):
                ctx_summary += f"\nปลายทาง: {ctx['destination']}"
            if ctx.get("travel_date"):
                ctx_summary += f"\nวันเดินทาง: {ctx['travel_date']}"
            if ctx.get("pax"):
                ctx_summary += f"\nจำนวน: {ctx['pax']} ท่าน"
            if ctx.get("budget_per_person"):
                ctx_summary += f"\nงบ: {ctx['budget_per_person']:,}" if isinstance(ctx['budget_per_person'], (int, float)) else f"\nงบ: {ctx['budget_per_person']}"
            # แสดงชื่อลูกค้าถ้ามี ไม่งั้นใช้ PSID ย่อ
            display_name = ctx.get("customer_name") or f"PSID ...{sender_id[-6:]}"
            case_id = get_or_create_case_id(sender_id, ctx)
            notify_line(
                f"{stage_emoji} Lead [{lead_stage.upper()}]\n"
                f"🆔 Case: {case_id}\n"
                f"👤 {display_name}\n"
                f"💬 {text}"
                f"{ctx_summary}\n"
                f"🔗 Dashboard: เปิด Lead Dashboard เพื่อ Pause/Resume Bot"
            )

        # Save user message
        save_to_history(sender_id, "user", text)

        # Generate response (with context injected into system prompt)
        reply = generate_response(text, history, tour_data, is_handoff, ctx=ctx, action=action, missing_field_to_ask=missing_field_to_ask)

        # Save AI reply
        save_to_history(sender_id, "assistant", reply)

        # Track pending_action — what AI just said it will do
        if action in ("search", "detail", "detail_pdf"):
            ctx["pending_action"] = f"{action}_{country_id or 'unknown'}"
        elif action == "handoff":
            ctx["pending_action"] = "handoff_sent"
            # Pause bot — human takes over for 2h (covers handoff_requested + booking)
            pause_reason = "booking" if lead_stage == "booking" else "handoff_requested"
            pause_bot(sender_id, ctx, pause_reason, hours=2)
        elif action == "flash_sale":
            ctx["pending_action"] = "flash_sale"  # maintain context for next turn
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
                is_handoff_action = (action == "handoff")
                if is_handoff_action:
                    new_ctx["_lead_handoff_requested"] = True
                    new_ctx["_lead_handoff_at"] = datetime.now().isoformat()
                    new_ctx["_lead_status"] = "waiting_team"
                    new_ctx["_lead_channel"] = "messenger"

                if lead_stage in ("hot", "booking", "paid", "awaiting_docs", "complete") or is_handoff_action:
                    save_lead_supabase(sender_id, new_ctx, lead_stage, text)
                elif lead_stage == "warm" and new_ctx.get("destination"):
                    # warm leads with destination also worth tracking
                    new_ctx["_lead_channel"] = "messenger"
                    save_lead_supabase(sender_id, new_ctx, lead_stage, text)

                # Log chat event for dashboard
                threading.Thread(
                    target=log_chat_event,
                    args=(sender_id,),
                    kwargs={
                        "event_type":   "handoff" if is_handoff_action else ("user_message" if tour_data == "" else "search_result"),
                        "ctx":          new_ctx,
                        "message":      text,
                        "bot_reply":    reply[:600] if reply else "",
                        "intent":       action,
                        "needs_review": new_ctx.get("_lead_needs_review", False),
                        "review_reason":new_ctx.get("_lead_review_reason", ""),
                    },
                    daemon=True,
                ).start()
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
  .cold .num { color: #aaa; } .warm .num { color: #f59e0b; }
  .hot .num { color: #ef4444; } .booking .num { color: #10b981; }
  .section { padding: 0 28px 28px; }
  .section h2 { font-size: 1rem; margin-bottom: 14px; color: #444;
                border-left: 4px solid #e63946; padding-left: 10px; }
  table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 12px;
          overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
  th { background: #fef2f2; color: #666; font-size: 0.78rem; text-transform: uppercase;
       padding: 10px 14px; text-align: left; }
  td { padding: 8px 12px; font-size: 0.86rem; border-top: 1px solid #f3f4f6; vertical-align: middle; }
  tr:hover td { background: #fef9f9; }
  .badge { display: inline-block; border-radius: 6px; padding: 2px 8px;
           font-size: 0.75rem; font-weight: 600; }
  .badge-cold { background: #f3f4f6; color: #9ca3af; }
  .badge-warm { background: #fef3c7; color: #d97706; }
  .badge-hot  { background: #fee2e2; color: #dc2626; }
  .badge-booking { background: #d1fae5; color: #059669; }
  .badge-paused  { background: #fee2e2; color: #dc2626; }
  .badge-active  { background: #d1fae5; color: #059669; }
  .no-data { text-align: center; color: #aaa; padding: 40px; font-size: 0.9rem; }
  .refresh { float: right; background: #e63946; color: #fff; border: none; border-radius: 8px;
             padding: 6px 14px; cursor: pointer; font-size: 0.82rem; margin-top: -2px; }
  .refresh:hover { background: #c1121f; }
  .options-list { font-size: 0.76rem; color: #555; }
  .updated { font-size: 0.7rem; color: #aaa; }
  .avatar { width: 34px; height: 34px; border-radius: 50%; object-fit: cover; flex-shrink: 0; background: #e5e7eb; }
  .customer-cell { display: flex; align-items: center; gap: 8px; min-width: 130px; }
  .customer-info strong { display: block; font-size: 0.84rem; line-height: 1.2; }
  .psid-sub { font-size: 0.66rem; color: #bbb; }
  .btn-pause  { background: #f59e0b; color: #fff; border: none; border-radius: 5px;
                padding: 3px 7px; font-size: 0.68rem; cursor: pointer; display: block; margin: 2px 0; width: 62px; }
  .btn-pause:hover  { background: #d97706; }
  .btn-resume { background: #10b981; color: #fff; border: none; border-radius: 5px;
                padding: 3px 7px; font-size: 0.68rem; cursor: pointer; display: block; margin: 2px 0; width: 62px; }
  .btn-resume:hover { background: #059669; }
  #toast { position: fixed; bottom: 24px; right: 24px; background: #1f2937; color: #fff;
           padding: 12px 20px; border-radius: 10px; font-size: 0.84rem; display: none;
           z-index: 9999; box-shadow: 0 4px 16px rgba(0,0,0,0.25); max-width: 300px; }
  @media (max-width: 600px) {
    .stats { flex-direction: column; } table { font-size: 0.78rem; }
    td, th { padding: 6px 8px; } .customer-cell { flex-direction: column; align-items: flex-start; gap: 2px; }
  }
</style>
</head>
<body>
<header>
  <h1>&#128293; รวมทัวร์ไฟไหม้ — Lead Dashboard</h1>
  <p>ข้อมูล Lead จาก AI Sales Bot (Messenger)</p>
</header>

<div class="stats">
  <div class="stat-card booking">
    <div class="num">{{ counts.get('booking', 0) }}</div>
    <div class="label">&#128203; Booking</div>
  </div>
  <div class="stat-card hot">
    <div class="num">{{ counts.get('hot', 0) }}</div>
    <div class="label">&#128276; Hot</div>
  </div>
  <div class="stat-card warm">
    <div class="num">{{ counts.get('warm', 0) }}</div>
    <div class="label">&#128172; Warm</div>
  </div>
  <div class="stat-card cold">
    <div class="num">{{ counts.get('cold', 0) }}</div>
    <div class="label">&#10052;&#65039; Cold</div>
  </div>
</div>

<div class="section">
  <h2>&#128203; Booking + &#128276; Hot Leads
    <button class="refresh" onclick="location.reload()">&#128260; รีเฟรช</button>
  </h2>
  {% if hot_leads %}
  <table>
    <thead><tr>
      <th>Stage</th><th>ลูกค้า</th><th>เบอร์/LINE</th>
      <th>ปลายทาง</th><th>เดือน</th><th>งบ/คน</th><th>จำนวน</th>
      <th>โปรแกรม</th><th>ข้อความล่าสุด</th><th>บอท</th><th>อัปเดต</th><th>Action</th>
    </tr></thead>
    <tbody>
    {% for lead in hot_leads %}
      {% set psid_val = lead.get('psid') or '' %}
      {% set display_name = lead.get('full_name') or lead.get('customer_name') or ('ลูกค้าใหม่ (...' + psid_val[-4:] + ')') %}
      <tr>
        <td><span class="badge badge-{{ lead.lead_stage }}">{{ lead.lead_stage }}</span></td>
        <td>
          <div class="customer-cell">
            {% if lead.get('profile_pic') %}<img class="avatar" src="{{ lead.profile_pic }}" onerror="this.style.display='none'">{% endif %}
            <div class="customer-info">
              <strong>{{ display_name }}</strong>
              <span class="psid-sub">...{{ psid_val[-6:] }}</span>
            </div>
          </div>
        </td>
        <td>{{ lead.phone or '—' }}</td>
        <td>{{ lead.destination or '—' }}</td>
        <td>{{ lead.month or '—' }}</td>
        <td>{{ '{:,}'.format(lead.budget_per_person) if lead.budget_per_person else '—' }}</td>
        <td>{{ lead.pax or '—' }}</td>
        <td class="options-list">
          {% set opts = lead.last_options %}
          {% if opts %}{% for o in opts[:2] %}<div>{{ o.get('index','') }}. {{ o.get('name','')[:26] }}</div>{% endfor %}
          {% else %}—{% endif %}
        </td>
        <td>{{ (lead.last_message or '')[:50] }}{% if lead.last_message and lead.last_message|length > 50 %}…{% endif %}</td>
        <td>
          {% if lead.get('human_takeover') %}<span class="badge badge-paused">&#9208; หยุด</span>
          {% else %}<span class="badge badge-active">&#9654; เปิด</span>{% endif %}
        </td>
        <td class="updated">{{ lead.updated_at[:16].replace('T',' ') if lead.updated_at else '—' }}</td>
        <td>
          {% if psid_val %}
          <button class="btn-pause"  onclick="pauseBot('{{ psid_val }}',2)">&#9208; 2h</button>
          <button class="btn-pause"  onclick="pauseBot('{{ psid_val }}',24)">&#9208; 24h</button>
          <button class="btn-resume" onclick="resumeBot('{{ psid_val }}')">&#9654; เปิด</button>
          {% else %}—{% endif %}
        </td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
  <div class="no-data">ยังไม่มี Hot/Booking Leads</div>
  {% endif %}
</div>

<div class="section">
  <h2>&#128172; Warm Leads ล่าสุด</h2>
  {% if warm_leads %}
  <table>
    <thead><tr>
      <th>ลูกค้า</th><th>เบอร์/LINE</th><th>ปลายทาง</th>
      <th>ประเทศ</th><th>เดือน</th><th>งบ/คน</th><th>ข้อความล่าสุด</th><th>อัปเดต</th>
    </tr></thead>
    <tbody>
    {% for lead in warm_leads %}
      {% set psid_val = lead.get('psid') or '' %}
      {% set display_name = lead.get('full_name') or lead.get('customer_name') or ('ลูกค้าใหม่ (...' + psid_val[-4:] + ')') %}
      <tr>
        <td>
          <div class="customer-cell">
            {% if lead.get('profile_pic') %}<img class="avatar" src="{{ lead.profile_pic }}" onerror="this.style.display='none'">{% endif %}
            <div class="customer-info">
              <strong>{{ display_name }}</strong>
              <span class="psid-sub">...{{ psid_val[-6:] }}</span>
            </div>
          </div>
        </td>
        <td>{{ lead.phone or '—' }}</td>
        <td>{{ lead.destination or '—' }}</td>
        <td>{{ lead.country or '—' }}</td>
        <td>{{ lead.month or '—' }}</td>
        <td>{{ '{:,}'.format(lead.budget_per_person) if lead.budget_per_person else '—' }}</td>
        <td>{{ (lead.last_message or '')[:55] }}{% if lead.last_message and lead.last_message|length > 55 %}…{% endif %}</td>
        <td class="updated">{{ lead.updated_at[:16].replace('T',' ') if lead.updated_at else '—' }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
  <div class="no-data">ยังไม่มี Warm Leads</div>
  {% endif %}
</div>

<div style="text-align:center;padding:20px;color:#bbb;font-size:0.8rem;">
  รวมทัวร์ไฟไหม้ AI Sales Bot v3 • <a href="/health" style="color:#bbb">health check</a>
</div>

<div id="toast"></div>
<script>
const ADMIN_PASS = new URLSearchParams(window.location.search).get('pass') || '';
function showToast(msg, ok) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.background = ok ? '#059669' : '#dc2626';
  t.style.display = 'block';
  setTimeout(() => { t.style.display = 'none'; }, 3200);
}
async function pauseBot(psid, hours) {
  try {
    const r = await fetch('/admin/pause', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Pass': ADMIN_PASS },
      body: JSON.stringify({ psid, hours })
    });
    const d = await r.json();
    if (r.ok) { showToast('⏸ Bot หยุด ' + hours + 'h (' + psid.slice(-6) + ')', true); setTimeout(() => location.reload(), 1500); }
    else showToast('❌ ' + (d.error || 'error'), false);
  } catch(e) { showToast('❌ ' + e.message, false); }
}
async function resumeBot(psid) {
  try {
    const r = await fetch('/admin/resume', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Pass': ADMIN_PASS },
      body: JSON.stringify({ psid })
    });
    const d = await r.json();
    if (r.ok) { showToast('▶ Bot เปิดแล้ว (' + psid.slice(-6) + ')', true); setTimeout(() => location.reload(), 1500); }
    else showToast('❌ ' + (d.error || 'error'), false);
  } catch(e) { showToast('❌ ' + e.message, false); }
}
</script>
</body>
</html>"""

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

    # Enrich leads with customer profile data (name + profile pic)
    all_psids = list({l.get("psid", "") for l in booking_hot + warm if l.get("psid")})
    if all_psids:
        cust_map = fetch_customers_batch(all_psids)
        for lead in booking_hot + warm:
            cust = cust_map.get(lead.get("psid", ""), {})
            lead["full_name"]   = (cust.get("full_name") or cust.get("name") or
                                   lead.get("customer_name") or
                                   f"\u0e25\u0e39\u0e01\u0e04\u0e49\u0e32\u0e43\u0e2b\u0e21\u0e48 (...{lead.get('psid','????')[-4:]})")
            lead["profile_pic"] = cust.get("profile_pic", "")

    return render_template_string(
        DASHBOARD_HTML,
        counts=counts,
        hot_leads=booking_hot,
        warm_leads=warm,
        admin_pass=pw,
    )


# ─── Webhook routes ──────────────────────────────────────────────────
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
            if not sender_id:
                continue

            message      = msg_event.get("message", {})
            text         = message.get("text", "").strip()

            # ── Ad Attribution (referral / postback) — sync before process_message ──
            has_referral = (
                msg_event.get("referral") or
                msg_event.get("postback", {}).get("referral") or
                msg_event.get("ads_context_data")
            )
            if has_referral:
                capture_ad_attribution(sender_id, msg_event)  # sync ก่อน fork

            # Handle postback text if no regular text
            if not text and msg_event.get("postback", {}).get("title"):
                text = msg_event["postback"]["title"]

            # ─── Image attachment handling (New Policy) ─────────────────
            attachments = message.get("attachments", [])
            image_list  = [a for a in attachments if a.get("type") == "image"]
            if image_list:
                image_urls   = [a.get("payload", {}).get("url", "") for a in image_list]
                _img_text    = (text or "").strip()
                _is_payment  = any(kw in _img_text for kw in _PAYMENT_IMAGE_KEYWORDS)
                if _is_payment:
                    logger.info(f"💳 Payment image from {sender_id}: keyword detected")
                    t = threading.Thread(
                        target=process_payment_pending_review,
                        args=(sender_id, image_urls, _img_text)
                    )
                else:
                    logger.info(f"📷 General image from {sender_id} → image_handoff")
                    t = threading.Thread(
                        target=process_image_handoff,
                        args=(sender_id, image_urls, _img_text)
                    )
                t.daemon = True
                t.start()
                continue

            if not text:
                continue
            t = threading.Thread(target=process_message, args=(sender_id, text))
            t.daemon = True
            t.start()

    return jsonify({"status": "ok"}), 200



@app.route("/admin/pause", methods=["POST"])
def admin_pause():
    """Admin: หยุด bot สำหรับ PSID ที่ระบุ (X-Admin-Pass header required)"""
    auth = (request.headers.get("X-Admin-Pass") or
            (request.get_json(silent=True) or {}).get("pass", ""))
    if auth != DASHBOARD_PASS:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    psid = data.get("psid", "").strip()
    hours = max(1, min(168, int(data.get("hours", 2))))
    if not psid:
        return jsonify({"error": "psid required"}), 400
    ctx = get_context(psid)
    pause_bot(psid, ctx, "manual_admin_pause", hours=hours)
    save_context(psid, ctx)
    # Best-effort: update leads table
    try:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/leads",
            params={"psid": f"eq.{psid}"},
            json={"human_takeover": True},
            headers=_sb_headers(),
            timeout=6,
        )
    except Exception:
        pass
    logger.info(f"⛔ Admin paused bot: ...{psid[-6:]}, {hours}h")
    return jsonify({"status": "paused", "psid": psid,
                    "until": ctx.get("bot_paused_until"), "hours": hours})


@app.route("/admin/resume", methods=["POST"])
def admin_resume():
    """เปิด bot สำหรับ PSID ที่ระบุ (X-Admin-Pass header required)"""
    auth = (request.headers.get("X-Admin-Pass") or
            (request.get_json(silent=True) or {}).get("pass", ""))
    if auth != DASHBOARD_PASS:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    psid = data.get("psid", "").strip()
    if not psid:
        return jsonify({"error": "psid required"}), 400
    ctx = get_context(psid)
    ctx["human_takeover"]   = False
    ctx["bot_paused_until"] = None
    ctx["bot_pause_reason"] = None
    # legacy_conversation stays blocked unless manually cleared in future phase
    save_context(psid, ctx)
    # Best-effort: update leads table
    try:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/leads",
            params={"psid": f"eq.{psid}"},
            json={"human_takeover": False},
            headers=_sb_headers(),
            timeout=6,
        )
    except Exception:
        pass
    logger.info(f"▶ Admin resumed bot: ...{psid[-6:]}")
    return jsonify({"status": "resumed", "psid": psid})


@app.route("/test-line", methods=["GET"])
def test_line():
    """ทดสอบ LINE Messaging API — ส่งไป GROUP_ID ถ้ามี ไม่งั้นส่งไป ADMIN_ID"""
    if not LINE_CHANNEL_TOKEN:
        return jsonify({"error": "LINE_CHANNEL_TOKEN not set"}), 400
    target_id = LINE_GROUP_ID or LINE_ADMIN_ID
    if not target_id:
        return jsonify({"error": "LINE_GROUP_ID and LINE_ADMIN_ID are both unset"}), 400
    try:
        resp = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "to": target_id,
                "messages": [{"type": "text", "text": "🔧 ทดสอบระบบแจ้งเตือน LINE — TourFireMai Bot ✅"}]
            },
            timeout=10,
        )
        return jsonify({
            "status": resp.status_code,
            "line_response": resp.text[:300],
            "token_prefix": LINE_CHANNEL_TOKEN[:20] + "...",
            "target_id": target_id,
            "target_type": "group" if LINE_GROUP_ID else "admin",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/line-webhook", methods=["GET", "POST"])
def line_webhook():
    """LINE Messaging API Webhook — รับ events จาก LINE group"""
    if request.method == "GET":
        return jsonify({"status": "LINE webhook ready"}), 200

    try:
        body = request.get_json(force=True) or {}
        events = body.get("events", [])

        for event in events:
            source = event.get("source", {})
            source_type = source.get("type", "")   # user | group | room
            group_id    = source.get("groupId", "")
            user_id     = source.get("userId", "")
            event_type  = event.get("type", "")

            # Log Group ID ทุกครั้งที่มี event จากกลุ่ม
            if group_id:
                logger.info(f"🔑 LINE GROUP ID: {group_id} (type={source_type}, event={event_type})")

            # ถ้าเป็น message event — log เพิ่มเติม
            if event_type == "message":
                msg_text = event.get("message", {}).get("text", "")
                logger.info(f"📨 LINE group message from {user_id[:10]}: {msg_text[:50]}")

                # ถ้าพิมพ์ /groupid → ตอบกลับ Group ID ใน group
                if msg_text.strip() == "/groupid" and LINE_CHANNEL_TOKEN and group_id:
                    reply_token = event.get("replyToken", "")
                    if reply_token:
                        requests.post(
                            "https://api.line.me/v2/bot/message/reply",
                            headers={
                                "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "replyToken": reply_token,
                                "messages": [{
                                    "type": "text",
                                    "text": f"🔑 Group ID ของกลุ่มนี้:\n{group_id}\n\nคัดลอกไปใส่ใน Railway env:\nLINE_GROUP_ID={group_id}"
                                }]
                            },
                            timeout=10,
                        )

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.error(f"line_webhook error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "TourFiremai AI Concierge v5",
        "redis": "connected" if _redis else "in-memory",
        "supabase": "configured" if SUPABASE_URL else "not configured",
    }), 200


# ─── Entry point ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
