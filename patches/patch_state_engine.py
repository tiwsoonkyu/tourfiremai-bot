#!/usr/bin/env python3
"""
P0/P1 Tour State Engine Patch
Adds: save_offer_snapshot, load_offer_snapshot, resolve_tour_selection,
      _format_tour_selected_msg, _build_ambiguous_msg
Modifies: _EMPTY_CTX, process_message (early resolve + snapshot save)
"""
import re
import sys

with open("/tmp/app_current.py", "r", encoding="utf-8") as f:
    src = f.read()

original_len = len(src)

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 1: Add "conversation_state" to _EMPTY_CTX
# ─────────────────────────────────────────────────────────────────────────────

OLD_CTX_FIELD = '"lead_stage": None,           # cold/warm/hot/booking/paid/awaiting_docs/complete'
NEW_CTX_FIELD = '"lead_stage": None,           # cold/warm/hot/booking/paid/awaiting_docs/complete\n    "conversation_state": "browsing",  # browsing|options_presented|tour_selected|departure_selected|fee_check_required|handoff_waiting|paused_by_human'

assert OLD_CTX_FIELD in src, f"ABORT: cannot find lead_stage field in src"
src = src.replace(OLD_CTX_FIELD, NEW_CTX_FIELD, 1)
print("PATCH 1 OK: added conversation_state to _EMPTY_CTX")

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 2: Add new functions block just before "def process_message("
# ─────────────────────────────────────────────────────────────────────────────

NEW_FUNCTIONS = '''
# ═══════════════════════════════════════════════════════════════════════════
# TOUR STATE ENGINE — Offer Snapshot + Deterministic Resolver (P0/P1)
# ═══════════════════════════════════════════════════════════════════════════

def _offer_snapshot_redis_key(psid: str) -> str:
    return f"tourfiremai:offer_snapshot:{psid}:latest"


def save_offer_snapshot(psid: str, options_list: list, search_context: dict) -> str:
    """Save offer snapshot to Redis + Supabase. Returns offer_set_id."""
    import uuid as _uuid
    offer_set_id = str(_uuid.uuid4())

    # Add 1-based index to each option (max 3)
    options_with_idx = []
    for i, opt in enumerate(options_list[:3]):
        o = dict(opt)
        o["_offer_index"] = i + 1
        options_with_idx.append(o)

    snapshot = {
        "psid": psid,
        "offer_set_id": offer_set_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "expires_at": (datetime.utcnow() + timedelta(days=30)).isoformat() + "Z",
        "search_context": search_context,
        "options": options_with_idx,
    }

    # Save to Redis
    try:
        redis_client.setex(
            _offer_snapshot_redis_key(psid),
            REDIS_TTL_SEC,
            json.dumps(snapshot, ensure_ascii=False),
        )
    except Exception as _e:
        logger.error(f"save_offer_snapshot Redis error: {_e}")

    # Save to Supabase (fire-and-forget)
    try:
        _supa_url = os.environ.get("SUPABASE_URL", "")
        _supa_key = os.environ.get("SUPABASE_KEY", "")
        if _supa_url and _supa_key:
            requests.post(
                f"{_supa_url}/rest/v1/offer_snapshots",
                headers={
                    "apikey": _supa_key,
                    "Authorization": f"Bearer {_supa_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                json=snapshot,
                timeout=5,
            )
    except Exception as _e:
        logger.error(f"save_offer_snapshot Supabase error: {_e}")

    return offer_set_id


def load_offer_snapshot(psid: str) -> dict:
    """Load latest offer snapshot from Redis (fallback: Supabase)."""
    # Try Redis first
    try:
        raw = redis_client.get(_offer_snapshot_redis_key(psid))
        if raw:
            return json.loads(raw)
    except Exception as _e:
        logger.error(f"load_offer_snapshot Redis error: {_e}")

    # Fallback to Supabase
    try:
        _supa_url = os.environ.get("SUPABASE_URL", "")
        _supa_key = os.environ.get("SUPABASE_KEY", "")
        if _supa_url and _supa_key:
            _now = datetime.utcnow().isoformat() + "Z"
            resp = requests.get(
                f"{_supa_url}/rest/v1/offer_snapshots",
                headers={
                    "apikey": _supa_key,
                    "Authorization": f"Bearer {_supa_key}",
                },
                params={
                    "psid": f"eq.{psid}",
                    "expires_at": f"gte.{_now}",
                    "order": "created_at.desc",
                    "limit": "1",
                },
                timeout=5,
            )
            data = resp.json()
            if data and isinstance(data, list) and len(data) > 0:
                return data[0]
    except Exception as _e:
        logger.error(f"load_offer_snapshot Supabase fallback error: {_e}")

    return None


def resolve_tour_selection(text: str, snapshot: dict) -> dict:
    """
    Deterministically match user input to a tour option from offer_snapshot.
    Priority: 1=index, 2=web_code, 3=tour_code_real, 4=price, 5=name/keyword
    Returns:
      {"tour": <opt>, "index": int, "match_type": str}  — single match
      {"ambiguous": True, "matches": [...]}              — multiple matches
      {"tour": None, "match_type": None}                 — no match
    """
    if not snapshot or not snapshot.get("options"):
        return {"tour": None, "match_type": None}

    options = snapshot["options"]
    text_clean = text.strip()

    # ── Priority 1: Ordinal index ──────────────────────────────────────────
    _idx_digit_patterns = [
        r"ตัวที่\s*([123๑๒๓])",
        r"ตัว\s*([123๑๒๓])",
        r"ที่\s*([123๑๒๓])",
        r"อันที่\s*([123๑๒๓])",
        r"ข้อ\s*([123๑๒๓])",
        r"หมายเลข\s*([123๑๒๓])",
        r"^([123])\s*(?:ครับ|ค่ะ|นะ|เลย|ได้เลย)?$",
    ]
    _thai_digit = {"๑": "1", "๒": "2", "๓": "3"}
    for _pat in _idx_digit_patterns:
        _m = re.search(_pat, text_clean)
        if _m:
            _d = _m.group(1)
            _d = _thai_digit.get(_d, _d)
            try:
                _idx = int(_d)
                if 1 <= _idx <= len(options):
                    _opt = options[_idx - 1]
                    return {"tour": _opt, "index": _idx, "match_type": "index"}
            except (ValueError, IndexError):
                pass

    # Ordinal Thai words
    for _word, _idx in [("แรก", 1), ("สอง", 2), ("สาม", 3)]:
        if re.search(r"(?:ตัว|อัน)" + _word, text_clean):
            if 1 <= _idx <= len(options):
                return {"tour": options[_idx - 1], "index": _idx, "match_type": "index"}

    # ── Priority 2: Exact web_code (e.g. ap182712) ────────────────────────
    for _i, _opt in enumerate(options):
        _wc = (_opt.get("web_code") or "").strip()
        if _wc and _wc.lower() in text_clean.lower():
            return {"tour": _opt, "index": _i + 1, "match_type": "web_code"}

    # ── Priority 3: Exact tour_code_real (e.g. CVZFUK3) ──────────────────
    for _i, _opt in enumerate(options):
        _tc = (_opt.get("tour_code_real") or _opt.get("tour_code") or "").strip()
        if _tc and _tc.upper() in text_clean.upper():
            return {"tour": _opt, "index": _i + 1, "match_type": "tour_code"}

    # ── Priority 4: Price exact match ─────────────────────────────────────
    _price_matches = []
    for _i, _opt in enumerate(options):
        _price = _opt.get("price_min") or _opt.get("promo_price")
        if _price:
            _price_str = str(int(float(_price)))
            if _price_str in re.sub(r"[,\s]", "", text_clean):
                _price_matches.append((_i + 1, _opt))
    if len(_price_matches) == 1:
        return {"tour": _price_matches[0][1], "index": _price_matches[0][0], "match_type": "price"}
    elif len(_price_matches) > 1:
        return {
            "ambiguous": True,
            "matches": [{"index": _i, "tour": _t} for _i, _t in _price_matches],
        }

    # ── Priority 5: Route / name keyword match ────────────────────────────
    _name_matches = []
    for _i, _opt in enumerate(options):
        _name = (_opt.get("name") or "")
        # Extract Thai words >= 3 chars from tour name
        _name_words = re.findall(r"[฀-๿]{3,}", _name)
        for _word in _name_words:
            if _word in text_clean:
                _name_matches.append((_i + 1, _opt))
                break
    if len(_name_matches) == 1:
        return {"tour": _name_matches[0][1], "index": _name_matches[0][0], "match_type": "name_keyword"}
    elif len(_name_matches) > 1:
        return {
            "ambiguous": True,
            "matches": [{"index": _i, "tour": _t} for _i, _t in _name_matches],
        }

    return {"tour": None, "match_type": None}


def _format_tour_selected_msg(ctx: dict, selected_option: dict, resolution: dict) -> str:
    """Format the structured tour-selection confirmation message."""
    _name = selected_option.get("name", "โปรแกรมที่เลือก")
    _tour_code_real = (selected_option.get("tour_code_real") or selected_option.get("tour_code") or "").strip()
    _web_code = (selected_option.get("web_code") or "").strip()
    _airline = (selected_option.get("airline") or "").strip()
    _price = selected_option.get("price_min") or selected_option.get("promo_price") or 0
    _url = (selected_option.get("url") or "").strip()
    _dates = selected_option.get("departure_dates") or []
    _idx = resolution.get("index", "")

    # Format dates
    if isinstance(_dates, list) and _dates:
        _dates_str = ", ".join(str(_d) for _d in _dates[:5])
        if len(_dates) > 5:
            _dates_str += f" (+{len(_dates)-5} วัน)"
    elif isinstance(_dates, str) and _dates:
        _dates_str = _dates
    else:
        _dates_str = "กรุณาสอบถาม"

    _cname = ctx.get("customer_name") or ""
    _greeting = f"ได้เลยค่ะ คุณ{_cname} \U0001F60A" if _cname else "ได้เลยค่ะ \U0001F60A"

    _parts = [_greeting]
    if _idx:
        _parts.append(f"เลือกตัวที่ {_idx}: {_name}")
    else:
        _parts.append(f"เลือก: {_name}")
    if _tour_code_real:
        _parts.append(f"\U0001F3F7 รหัสทัวร์: {_tour_code_real}")
    if _web_code:
        _parts.append(f"\U0001F511 รหัสเว็บ: {_web_code}")
    if _airline:
        _parts.append(f"✈️ สายการบิน: {_airline}")
    if _dates_str:
        _parts.append(f"\U0001F4C5 วันเดินทางที่มี: {_dates_str}")
    if _price:
        _parts.append(f"\U0001F4B0 ราคาเริ่ม: {int(float(_price)):,} บาท")
    if _url:
        _parts.append(f"\U0001F517 {_url}")
    _parts.append("\\nสนใจเดินทางวันไหนคะ?")

    return "\\n".join(_parts)


def _build_ambiguous_msg(matches: list) -> str:
    """Ask user to clarify between ambiguous option matches."""
    _lines = ["มีหลายโปรแกรมที่ตรงกัน กรุณาระบุด้วยนะคะ:"]
    for _m in matches:
        _i = _m.get("index", "?")
        _t = _m.get("tour", {})
        _name = _t.get("name", f"โปรแกรม {_i}")
        _price = _t.get("price_min") or _t.get("promo_price") or 0
        _price_str = f"{int(float(_price)):,} บาท" if _price else ""
        _lines.append(f"  ตัวที่ {_i}: {_name}" + (f" ({_price_str})" if _price_str else ""))
    return "\\n".join(_lines)


'''

ANCHOR_PROCESS_MESSAGE = "def process_message("
assert ANCHOR_PROCESS_MESSAGE in src, "ABORT: cannot find 'def process_message('"
src = src.replace(ANCHOR_PROCESS_MESSAGE, NEW_FUNCTIONS + ANCHOR_PROCESS_MESSAGE, 1)
print("PATCH 2 OK: added 7 new Tour State Engine functions")

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 3: Early-resolve block in process_message
# Insert BEFORE the existing smart-resolver block
# ─────────────────────────────────────────────────────────────────────────────

EARLY_RESOLVE_ANCHOR = '        if not ctx.get("selected_tour") and ctx.get("last_options") and not _direct_country_fill:'

EARLY_RESOLVE_BLOCK = '''        # ═══════════════════════════════════════════════════════════════
        # TOUR STATE ENGINE: Deterministic early-resolve from offer_snapshot
        # Runs before LLM classification when we are in options_presented state
        # or whenever a snapshot exists. Never resets context; never asks budget/country.
        # ═══════════════════════════════════════════════════════════════
        _state_engine_fired = False
        _offer_snap = None
        if ctx.get("conversation_state") in ("options_presented", "tour_selected") or ctx.get("last_options"):
            _offer_snap = load_offer_snapshot(sender_id)
        if _offer_snap and not ctx.get("booking_context_locked") and not _direct_country_fill:
            _resolution = resolve_tour_selection(text, _offer_snap)
            if _resolution.get("ambiguous"):
                _amb_msg = _build_ambiguous_msg(_resolution["matches"])
                save_to_history(sender_id, "assistant", _amb_msg)
                save_context(sender_id, ctx)
                send_message(sender_id, _amb_msg)
                return
            elif _resolution.get("tour"):
                _sel = _resolution["tour"]
                # Lock selection into context
                ctx["selected_tour"] = _sel
                ctx["selected_tour_name"] = _sel.get("name", "")
                ctx["selected_tour_url"] = _sel.get("url", "")
                ctx["selected_tour_code"] = (_sel.get("tour_code_real") or _sel.get("tour_code") or "")
                ctx["selected_tour_web_code"] = _sel.get("web_code", "")
                ctx["selected_tour_airline"] = _sel.get("airline", "")
                ctx["booking_context_locked"] = True
                ctx["conversation_state"] = "tour_selected"
                _confirm_msg = _format_tour_selected_msg(ctx, _sel, _resolution)
                save_context(sender_id, ctx)
                save_to_history(sender_id, "assistant", _confirm_msg)
                send_message(sender_id, _confirm_msg)
                _state_engine_fired = True
                return
        # ═══════════════════════════════════════════════════════════════

'''

assert EARLY_RESOLVE_ANCHOR in src, f"ABORT: cannot find early-resolve anchor"
src = src.replace(EARLY_RESOLVE_ANCHOR,
                  EARLY_RESOLVE_BLOCK + EARLY_RESOLVE_ANCHOR, 1)
print("PATCH 3 OK: early-resolve block inserted before existing smart-resolver")

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 4a: After ctx["last_options"] = tour_meta in the search path
# The line context: "                    ctx["last_options"] = tour_meta\n"
# followed by "                    if city_hint:"
# ─────────────────────────────────────────────────────────────────────────────

LAST_OPTIONS_SEARCH_OLD = '                    ctx["last_options"] = tour_meta\n                    if city_hint:'
LAST_OPTIONS_SEARCH_NEW = '''                    ctx["last_options"] = tour_meta
                    # TOUR STATE ENGINE: save offer snapshot for deterministic future selection
                    try:
                        _snap_search_ctx = {
                            "country": ctx.get("country_name", ""),
                            "city": ctx.get("city_hint", ""),
                            "budget_per_person": ctx.get("budget_per_person"),
                            "search_mode": ctx.get("search_mode", "normal"),
                        }
                        save_offer_snapshot(sender_id, tour_meta[:3], _snap_search_ctx)
                        ctx["conversation_state"] = "options_presented"
                    except Exception as _snap_err:
                        logger.error(f"save_offer_snapshot (search path) error: {_snap_err}")
                    if city_hint:'''

count_search = src.count(LAST_OPTIONS_SEARCH_OLD)
if count_search == 1:
    src = src.replace(LAST_OPTIONS_SEARCH_OLD, LAST_OPTIONS_SEARCH_NEW, 1)
    print(f"PATCH 4a OK: offer snapshot save inserted after last_options=tour_meta")
elif count_search > 1:
    print(f"WARNING: found {count_search} occurrences, using first")
    src = src.replace(LAST_OPTIONS_SEARCH_OLD, LAST_OPTIONS_SEARCH_NEW, 1)
    print("PATCH 4a OK (first occurrence)")
else:
    print("WARNING PATCH 4a: anchor not found — trying fallback")
    LAST_OPTIONS_SEARCH_OLD_FB = '                    ctx["last_options"] = tour_meta\n'
    cnt_fb = src.count(LAST_OPTIONS_SEARCH_OLD_FB)
    print(f"  Fallback occurrences: {cnt_fb}")
    if cnt_fb == 1:
        LAST_OPTIONS_SEARCH_NEW_FB = '''                    ctx["last_options"] = tour_meta
                    # TOUR STATE ENGINE: save offer snapshot for deterministic future selection
                    try:
                        _snap_search_ctx = {
                            "country": ctx.get("country_name", ""),
                            "city": ctx.get("city_hint", ""),
                            "budget_per_person": ctx.get("budget_per_person"),
                            "search_mode": ctx.get("search_mode", "normal"),
                        }
                        save_offer_snapshot(sender_id, tour_meta[:3], _snap_search_ctx)
                        ctx["conversation_state"] = "options_presented"
                    except Exception as _snap_err:
                        logger.error(f"save_offer_snapshot (search path) error: {_snap_err}")
'''
        src = src.replace(LAST_OPTIONS_SEARCH_OLD_FB, LAST_OPTIONS_SEARCH_NEW_FB, 1)
        print("PATCH 4a OK (fallback): offer snapshot save inserted")
    else:
        print(f"PATCH 4a SKIPPED: could not find suitable anchor (cnt_fb={cnt_fb})")

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 4b: After ctx["last_options"] = _flash_meta in the flash_sale path
# ─────────────────────────────────────────────────────────────────────────────

LAST_OPTIONS_FLASH_OLD = '                        ctx["last_options"] = _flash_meta\n                        ctx["pending_action"] = "wait_user"'
LAST_OPTIONS_FLASH_NEW = '''                        ctx["last_options"] = _flash_meta
                        # TOUR STATE ENGINE: save offer snapshot for flash_sale path
                        try:
                            _snap_flash_ctx = {
                                "country": ctx.get("country_name", ""),
                                "city": ctx.get("city_hint", ""),
                                "budget_per_person": ctx.get("budget_per_person"),
                                "search_mode": "faimai",
                            }
                            save_offer_snapshot(sender_id, _flash_meta[:3], _snap_flash_ctx)
                            ctx["conversation_state"] = "options_presented"
                        except Exception as _snap_flash_err:
                            logger.error(f"save_offer_snapshot (flash path) error: {_snap_flash_err}")
                        ctx["pending_action"] = "wait_user"'''

count_flash = src.count(LAST_OPTIONS_FLASH_OLD)
if count_flash >= 1:
    src = src.replace(LAST_OPTIONS_FLASH_OLD, LAST_OPTIONS_FLASH_NEW, 1)
    print(f"PATCH 4b OK: offer snapshot save inserted after last_options=_flash_meta")
else:
    print("INFO PATCH 4b: exact anchor not found — trying looser match")
    LAST_OPTIONS_FLASH_OLD2 = '                        ctx["last_options"] = _flash_meta\n'
    cnt2 = src.count(LAST_OPTIONS_FLASH_OLD2)
    if cnt2 == 1:
        LAST_OPTIONS_FLASH_NEW2 = '''                        ctx["last_options"] = _flash_meta
                        # TOUR STATE ENGINE: save offer snapshot for flash_sale path
                        try:
                            _snap_flash_ctx = {
                                "country": ctx.get("country_name", ""),
                                "city": ctx.get("city_hint", ""),
                                "budget_per_person": ctx.get("budget_per_person"),
                                "search_mode": "faimai",
                            }
                            save_offer_snapshot(sender_id, _flash_meta[:3], _snap_flash_ctx)
                            ctx["conversation_state"] = "options_presented"
                        except Exception as _snap_flash_err:
                            logger.error(f"save_offer_snapshot (flash path) error: {_snap_flash_err}")
'''
        src = src.replace(LAST_OPTIONS_FLASH_OLD2, LAST_OPTIONS_FLASH_NEW2, 1)
        print("PATCH 4b OK (fallback): offer snapshot save inserted")
    else:
        print(f"PATCH 4b SKIPPED: _flash_meta pattern has {cnt2} occurrences")

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 5: Update conversation_state after booking is locked (OPTION_SELECT path)
# Anchor: the specific pattern inside the OPTION_SELECT block
# ─────────────────────────────────────────────────────────────────────────────

BOOKING_LOCK_OLD = '''                # Lock booking context
                ctx["booking_context_locked"] = True
                if not ctx.get("booking_fields"):
                    ctx["booking_fields"] = {}
                ctx["pending_action"] = "collect_booking_info"
                ctx["pre_booking_detail_sent"] = False  # reset on new selection'''

BOOKING_LOCK_NEW = '''                # Lock booking context
                ctx["booking_context_locked"] = True
                ctx["conversation_state"] = "tour_selected"
                if not ctx.get("booking_fields"):
                    ctx["booking_fields"] = {}
                ctx["pending_action"] = "collect_booking_info"
                ctx["pre_booking_detail_sent"] = False  # reset on new selection'''

if BOOKING_LOCK_OLD in src:
    src = src.replace(BOOKING_LOCK_OLD, BOOKING_LOCK_NEW, 1)
    print("PATCH 5 OK: conversation_state=tour_selected added to booking lock block")
else:
    print("INFO PATCH 5: exact anchor not found — trying alternative")
    # Try the CODE_LOCK block at line ~3341
    BOOKING_LOCK_OLD2 = '                ctx["booking_context_locked"] = True\n                if not ctx.get("booking_fields"):\n                    ctx["booking_fields"] = {}\n                ctx["pre_booking_detail_sent"] = False  # reset on new selection'
    if BOOKING_LOCK_OLD2 in src:
        BOOKING_LOCK_NEW2 = '                ctx["booking_context_locked"] = True\n                ctx["conversation_state"] = "tour_selected"\n                if not ctx.get("booking_fields"):\n                    ctx["booking_fields"] = {}\n                ctx["pre_booking_detail_sent"] = False  # reset on new selection'
        src = src.replace(BOOKING_LOCK_OLD2, BOOKING_LOCK_NEW2, 1)
        print("PATCH 5 OK (alt): conversation_state=tour_selected added")
    else:
        print("INFO PATCH 5: booking_context_locked block not matched — skipping")

# ─────────────────────────────────────────────────────────────────────────────
# Verify result
# ─────────────────────────────────────────────────────────────────────────────

assert "conversation_state" in src, "VERIFY FAIL: conversation_state not in output"
assert "save_offer_snapshot" in src, "VERIFY FAIL: save_offer_snapshot not in output"
assert "load_offer_snapshot" in src, "VERIFY FAIL: load_offer_snapshot not in output"
assert "resolve_tour_selection" in src, "VERIFY FAIL: resolve_tour_selection not in output"
assert "_format_tour_selected_msg" in src, "VERIFY FAIL: _format_tour_selected_msg not in output"
assert "_build_ambiguous_msg" in src, "VERIFY FAIL: _build_ambiguous_msg not in output"
assert "TOUR STATE ENGINE: Deterministic early-resolve" in src, "VERIFY FAIL: early-resolve block not in output"
assert "TOUR STATE ENGINE: save offer snapshot" in src, "VERIFY FAIL: snapshot save not in output"

with open("/tmp/app_patched.py", "w", encoding="utf-8") as f:
    f.write(src)

new_len = len(src)
print(f"\nPatch complete: {original_len} -> {new_len} bytes (+{new_len - original_len})")
print(f"   Lines: {src.count(chr(10))}")
