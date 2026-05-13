#!/usr/bin/env python3
"""
P0 Hotfix: offer_snapshot reliability + history rebuild + atomic save + structured logs
Root cause: save_offer_snapshot / load_offer_snapshot used `redis_client` (NameError)
            instead of `_redis`. This silently failed every Redis call so snapshots
            were never saved or loaded.
"""

src = open('/tmp/app_cur.py', 'r', encoding='utf-8').read()
original_len = len(src)

# ────────────────────────────────────────────────────────────────────────────
# A+B+C: Replace save_offer_snapshot, load_offer_snapshot,
#        and INSERT _rebuild_snapshot_from_history before them
# ────────────────────────────────────────────────────────────────────────────

OLD_SNAPSHOT_BLOCK = '''def save_offer_snapshot(psid: str, options_list: list, search_context: dict) -> str:
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

    return None'''

NEW_SNAPSHOT_BLOCK = '''def _rebuild_snapshot_from_history(psid: str) -> dict:
    """
    Emergency fallback: rebuild offer_snapshot by parsing the last assistant
    message that contained a Top-3 tour listing from conversation history.
    Parses: web_code (ap...), tour index markers, prices, URLs from raw text.
    """
    try:
        history = get_history(psid)
        if not history:
            logger.warning("OFFER_SNAPSHOT_REBUILD_FAIL psid=%s reason=no_history", psid)
            return None

        # Iterate newest-first, look for assistant messages with Top-3 tour listing
        for msg in reversed(history):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", "")
            # Check for tour listing markers
            has_tour_list = any(m in content for m in [
                "ตัวที่ 1", "ตัวที่ 2", "ตัวที่ 3",
                "รหัสเว็บ: ap", "/tour/ap",
            ]) or bool(re.search(r'\\bap\\d{5,8}\\b', content))

            if not has_tour_list:
                continue

            # Parse each "ตัวที่ N" block
            options_with_idx = []
            # Split by "ตัวที่ N" markers
            blocks = re.split(r'(?=ตัวที่\\s*[123๑๒๓])', content)
            for block in blocks:
                m_idx = re.search(r'ตัวที่\\s*([123๑๒๓])', block)
                if not m_idx:
                    continue
                idx_char = m_idx.group(1)
                idx_map = {'1': 1, '2': 2, '3': 3, '๑': 1, '๒': 2, '๓': 3}
                offer_idx = idx_map.get(idx_char, 0)
                if offer_idx == 0:
                    continue

                # Extract web_code
                web_code = ""
                m_wc = re.search(r'(?:รหัสเว็บ:\\s*|/tour/)(ap\\d{5,8})', block)
                if m_wc:
                    web_code = m_wc.group(1)
                else:
                    m_wc2 = re.search(r'\\b(ap\\d{5,8})\\b', block)
                    if m_wc2:
                        web_code = m_wc2.group(1)

                # Extract tour_code_real
                tour_code_real = ""
                m_tc = re.search(r'รหัสทัวร์:\\s*([A-Z0-9\\-]{4,20})', block)
                if m_tc:
                    tour_code_real = m_tc.group(1)

                # Extract name (first line after "ตัวที่ N" marker)
                name = ""
                m_name = re.search(r'ตัวที่\\s*[123๑๒๓][:\\s]+(.+?)(?:\\n|$)', block)
                if m_name:
                    name = m_name.group(1).strip().rstrip('\\u200b').strip()

                # Extract price
                price_min = 0
                m_price = re.search(r'(?:ราคา|เริ่ม)\\D*?([\\d,]+)\\s*บาท', block)
                if m_price:
                    try:
                        price_min = int(m_price.group(1).replace(',', ''))
                    except ValueError:
                        pass

                # Extract airline
                airline = ""
                m_al = re.search(r'สายการบิน:\\s*([A-Z]{2,3})', block)
                if m_al:
                    airline = m_al.group(1)

                # Extract URL
                url = ""
                m_url = re.search(r'(https?://[^\\s]+)', block)
                if m_url:
                    url = m_url.group(1).rstrip(')')

                if web_code or name or price_min:
                    options_with_idx.append({
                        "_offer_index": offer_idx,
                        "web_code": web_code,
                        "tour_code_real": tour_code_real,
                        "name": name,
                        "price_min": price_min,
                        "airline": airline,
                        "url": url,
                    })

            if not options_with_idx:
                continue

            # Sort by _offer_index
            options_with_idx.sort(key=lambda x: x["_offer_index"])

            import uuid as _uuid2
            rebuilt_snapshot = {
                "psid": psid,
                "offer_set_id": str(_uuid2.uuid4()),
                "created_at": datetime.utcnow().isoformat() + "Z",
                "expires_at": (datetime.utcnow() + timedelta(days=30)).isoformat() + "Z",
                "search_context": {"rebuilt_from_history": True},
                "options": options_with_idx,
            }

            # Cache rebuilt snapshot in Redis for future loads
            if _redis:
                try:
                    _redis.setex(
                        _offer_snapshot_redis_key(psid),
                        REDIS_TTL_SEC,
                        json.dumps(rebuilt_snapshot, ensure_ascii=False),
                    )
                    logger.info("OFFER_SNAPSHOT_REBUILD_CACHED psid=%s options=%d", psid, len(options_with_idx))
                except Exception as _ce:
                    logger.warning("OFFER_SNAPSHOT_REBUILD_CACHE_FAIL psid=%s error=%s", psid, _ce)

            logger.info("OFFER_SNAPSHOT_LOAD source=rebuilt_history psid=%s options=%d", psid, len(options_with_idx))
            return rebuilt_snapshot

    except Exception as _re:
        logger.error("OFFER_SNAPSHOT_REBUILD_ERROR psid=%s error=%s", psid, _re)

    return None


def save_offer_snapshot(psid: str, options_list: list, search_context: dict) -> tuple:
    """Save offer snapshot to Redis + Supabase.
    Returns (offer_set_id, redis_ok, supabase_ok) tuple."""
    import uuid as _uuid
    offer_set_id = str(_uuid.uuid4())
    redis_ok = False
    supabase_ok = False

    opts = options_list[:3]
    logger.info("OFFER_SNAPSHOT_BUILD count=%d psid=%s offer_set_id=%s", len(opts), psid, offer_set_id)

    # Add 1-based index to each option (max 3)
    options_with_idx = []
    for i, opt in enumerate(opts):
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
    if _redis:
        try:
            _redis.setex(
                _offer_snapshot_redis_key(psid),
                REDIS_TTL_SEC,
                json.dumps(snapshot, ensure_ascii=False),
            )
            redis_ok = True
            logger.info("OFFER_SNAPSHOT_REDIS_SAVE success psid=%s", psid)
        except Exception as _e:
            logger.warning("OFFER_SNAPSHOT_REDIS_SAVE fail psid=%s error=%s", psid, _e)
    else:
        logger.warning("OFFER_SNAPSHOT_REDIS_SAVE skip psid=%s reason=no_redis_client", psid)

    # Save to Supabase (fire-and-forget)
    try:
        _supa_url = os.environ.get("SUPABASE_URL", "")
        _supa_key = os.environ.get("SUPABASE_KEY", "")
        if _supa_url and _supa_key:
            _resp = requests.post(
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
            if _resp.status_code in (200, 201):
                supabase_ok = True
                logger.info("OFFER_SNAPSHOT_SUPABASE_SAVE success psid=%s", psid)
            else:
                logger.warning("OFFER_SNAPSHOT_SUPABASE_SAVE fail psid=%s status=%d body=%s",
                               psid, _resp.status_code, _resp.text[:200])
        else:
            logger.warning("OFFER_SNAPSHOT_SUPABASE_SAVE skip psid=%s reason=no_supabase_config", psid)
    except Exception as _e:
        logger.warning("OFFER_SNAPSHOT_SUPABASE_SAVE fail psid=%s error=%s", psid, _e)

    return offer_set_id, redis_ok, supabase_ok


def load_offer_snapshot(psid: str) -> dict:
    """Load latest offer snapshot from Redis (fallback: Supabase, then history rebuild)."""
    # Try Redis first
    if _redis:
        try:
            raw = _redis.get(_offer_snapshot_redis_key(psid))
            if raw:
                logger.info("OFFER_SNAPSHOT_LOAD source=redis psid=%s", psid)
                return json.loads(raw)
        except Exception as _e:
            logger.error("load_offer_snapshot Redis error psid=%s error=%s", psid, _e)
    else:
        logger.warning("OFFER_SNAPSHOT_LOAD skip_redis psid=%s reason=no_redis_client", psid)

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
                logger.info("OFFER_SNAPSHOT_LOAD source=supabase psid=%s", psid)
                return data[0]
    except Exception as _e:
        logger.error("load_offer_snapshot Supabase fallback error psid=%s error=%s", psid, _e)

    # Final fallback: rebuild from conversation history
    logger.warning("OFFER_SNAPSHOT_LOAD miss psid=%s — trying history rebuild", psid)
    rebuilt = _rebuild_snapshot_from_history(psid)
    if rebuilt:
        return rebuilt

    logger.warning("OFFER_SNAPSHOT_LOAD miss psid=%s — no snapshot found anywhere", psid)
    return None'''

if OLD_SNAPSHOT_BLOCK in src:
    src = src.replace(OLD_SNAPSHOT_BLOCK, NEW_SNAPSHOT_BLOCK)
    print("✅ Replaced save_offer_snapshot + load_offer_snapshot + added _rebuild_snapshot_from_history")
else:
    print("❌ OLD_SNAPSHOT_BLOCK not found — checking partial match")
    # Partial check
    for fragment in ["def save_offer_snapshot", "redis_client.setex", "redis_client.get"]:
        idx = src.find(fragment)
        print(f"  '{fragment}' found at index: {idx}")

# ────────────────────────────────────────────────────────────────────────────
# D: Update resolve_tour_selection — add structured logging
# ────────────────────────────────────────────────────────────────────────────

OLD_RESOLVE = '''def resolve_tour_selection(text: str, snapshot: dict) -> dict:
    """
    Deterministically match user input to a tour option from offer_snapshot.
    Priority: 1=index, 2=web_code, 3=tour_code_real, 4=price, 5=name/keyword
    Returns:
      {"tour": <opt>, "index": int, "match_type": str}  — single match
      {"ambiguous": True, "matches": [...]}              — multiple matches
      {"tour": None, "match_type": None}                 — no match
    """
    if not snapshot or not snapshot.get("options"):
        return {"tour": None, "match_type": None}'''

NEW_RESOLVE = '''def resolve_tour_selection(text: str, snapshot: dict) -> dict:
    """
    Deterministically match user input to a tour option from offer_snapshot.
    Priority: 1=index, 2=web_code, 3=tour_code_real, 4=price, 5=name/keyword
    Returns:
      {"tour": <opt>, "index": int, "match_type": str}  — single match
      {"ambiguous": True, "matches": [...]}              — multiple matches
      {"tour": None, "match_type": None}                 — no match
    """
    logger.debug("OFFER_SELECTION_ATTEMPT attempting text='%s'", text[:50])
    if not snapshot or not snapshot.get("options"):
        logger.info("OFFER_SELECTION_FAILED reason=no_snapshot text='%s'", text[:50])
        return {"tour": None, "match_type": None}'''

if OLD_RESOLVE in src:
    src = src.replace(OLD_RESOLVE, NEW_RESOLVE)
    print("✅ Updated resolve_tour_selection with structured logging")
else:
    print("❌ OLD_RESOLVE not found")

# ────────────────────────────────────────────────────────────────────────────
# D2: Add success/fail logging at resolve_tour_selection return points
# We need to find the return statements and add logging before them.
# The function returns dicts — we wrap by modifying the final return None block.
# ────────────────────────────────────────────────────────────────────────────

# Find resolve_tour_selection and add logging at match success points.
# The function has multiple return paths. We'll find the "no match" return.
OLD_RESOLVE_NOMATCH = '''    # ── No match ──────────────────────────────────────────────────────────
    return {"tour": None, "match_type": None}'''

NEW_RESOLVE_NOMATCH = '''    # ── No match ──────────────────────────────────────────────────────────
    logger.info("OFFER_SELECTION_FAILED reason=no_match text='%s'", text[:50])
    return {"tour": None, "match_type": None}'''

if OLD_RESOLVE_NOMATCH in src:
    src = src.replace(OLD_RESOLVE_NOMATCH, NEW_RESOLVE_NOMATCH)
    print("✅ Added OFFER_SELECTION_FAILED log at no-match return")
else:
    print("❌ OLD_RESOLVE_NOMATCH not found — searching for alternatives")
    # Try to find any "no match" return in the resolve function
    idx = src.find('{"tour": None, "match_type": None}', src.find('def resolve_tour_selection'))
    print(f"  First 'no match' return found at index: {idx}")

# ────────────────────────────────────────────────────────────────────────────
# E+F: Fix the early-resolve block + search path atomic save
# ────────────────────────────────────────────────────────────────────────────

# Fix early-resolve block to add STATE_ENGINE_FIRED log
OLD_STATE_ENGINE = '''        if _offer_snap and not ctx.get("booking_context_locked") and not _direct_country_fill:
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
                return'''

NEW_STATE_ENGINE = '''        if _offer_snap and not ctx.get("booking_context_locked") and not _direct_country_fill:
            _resolution = resolve_tour_selection(text, _offer_snap)
            if _resolution.get("ambiguous"):
                _amb_msg = _build_ambiguous_msg(_resolution["matches"])
                save_to_history(sender_id, "assistant", _amb_msg)
                save_context(sender_id, ctx)
                send_message(sender_id, _amb_msg)
                return
            elif _resolution.get("tour"):
                logger.info("STATE_ENGINE_FIRED psid=%s resolution_type=%s", sender_id, _resolution.get("match_type"))
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
                logger.info("OFFER_SELECTION_RESOLVED index=%s match_type=%s psid=%s",
                            _sel.get("_offer_index"), _resolution.get("match_type"), sender_id)
                return'''

if OLD_STATE_ENGINE in src:
    src = src.replace(OLD_STATE_ENGINE, NEW_STATE_ENGINE)
    print("✅ Updated early-resolve block with STATE_ENGINE_FIRED + OFFER_SELECTION_RESOLVED logs")
else:
    print("❌ OLD_STATE_ENGINE not found")

# ────────────────────────────────────────────────────────────────────────────
# F: Fix atomic save in search path — handle return tuple from save_offer_snapshot
# ────────────────────────────────────────────────────────────────────────────

OLD_SEARCH_SAVE = '''                if tour_meta:
                    ctx["last_options"] = tour_meta
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
                        logger.error(f"save_offer_snapshot (search path) error: {_snap_err}")'''

NEW_SEARCH_SAVE = '''                if tour_meta:
                    ctx["last_options"] = tour_meta
                    # TOUR STATE ENGINE: save offer snapshot for deterministic future selection
                    try:
                        _snap_search_ctx = {
                            "country": ctx.get("country_name", ""),
                            "city": ctx.get("city_hint", ""),
                            "budget_per_person": ctx.get("budget_per_person"),
                            "search_mode": ctx.get("search_mode", "normal"),
                        }
                        _snap_offer_set_id, _snap_redis_ok, _snap_supa_ok = save_offer_snapshot(
                            sender_id, tour_meta[:3], _snap_search_ctx
                        )
                        ctx["conversation_state"] = "options_presented"
                        ctx["current_offer_set_id"] = _snap_offer_set_id
                        if not _snap_redis_ok and not _snap_supa_ok:
                            logger.error("OFFER_SNAPSHOT_SAVE_FAIL_BOTH psid=%s offer_set_id=%s",
                                         sender_id, _snap_offer_set_id)
                            _save_fail_msg = "ขออภัยค่ะ ระบบบันทึกรายการทัวร์ขัดข้องชั่วคราว เดี๋ยวให้ทีมงานช่วยเช็กให้นะคะ"
                            send_message(sender_id, _save_fail_msg)
                            save_to_history(sender_id, "assistant", _save_fail_msg)
                            save_context(sender_id, ctx)
                            return
                        elif not _snap_redis_ok:
                            logger.warning("OFFER_SNAPSHOT_REDIS_FAIL_ONLY psid=%s — continuing with Supabase only",
                                           sender_id)
                    except Exception as _snap_err:
                        logger.error(f"save_offer_snapshot (search path) error: {_snap_err}")'''

if OLD_SEARCH_SAVE in src:
    src = src.replace(OLD_SEARCH_SAVE, NEW_SEARCH_SAVE)
    print("✅ Updated search path with atomic save + OFFER_SNAPSHOT_SAVE_FAIL_BOTH guard")
else:
    print("❌ OLD_SEARCH_SAVE not found")

# ────────────────────────────────────────────────────────────────────────────
# F2: Fix flash_sale path — handle return tuple
# ────────────────────────────────────────────────────────────────────────────

OLD_FLASH_SAVE = '''                        try:
                            _snap_flash_ctx = {
                                "country": ctx.get("country_name", ""),
                                "city": ctx.get("city_hint", ""),
                                "budget_per_person": ctx.get("budget_per_person"),
                                "search_mode": "faimai",
                            }
                            save_offer_snapshot(sender_id, _flash_meta[:3], _snap_flash_ctx)
                            ctx["conversation_state"] = "options_presented"
                        except Exception as _snap_flash_err:
                            logger.error(f"save_offer_snapshot (flash path) error: {_snap_flash_err}")'''

NEW_FLASH_SAVE = '''                        try:
                            _snap_flash_ctx = {
                                "country": ctx.get("country_name", ""),
                                "city": ctx.get("city_hint", ""),
                                "budget_per_person": ctx.get("budget_per_person"),
                                "search_mode": "faimai",
                            }
                            _snap_flash_id, _snap_flash_redis_ok, _snap_flash_supa_ok = save_offer_snapshot(
                                sender_id, _flash_meta[:3], _snap_flash_ctx
                            )
                            ctx["conversation_state"] = "options_presented"
                            ctx["current_offer_set_id"] = _snap_flash_id
                            if not _snap_flash_redis_ok and not _snap_flash_supa_ok:
                                logger.error("OFFER_SNAPSHOT_SAVE_FAIL_BOTH psid=%s path=flash",
                                             sender_id)
                            elif not _snap_flash_redis_ok:
                                logger.warning("OFFER_SNAPSHOT_REDIS_FAIL_ONLY psid=%s path=flash",
                                               sender_id)
                        except Exception as _snap_flash_err:
                            logger.error(f"save_offer_snapshot (flash path) error: {_snap_flash_err}")'''

if OLD_FLASH_SAVE in src:
    src = src.replace(OLD_FLASH_SAVE, NEW_FLASH_SAVE)
    print("✅ Updated flash_sale path with tuple handling")
else:
    print("❌ OLD_FLASH_SAVE not found")

# ────────────────────────────────────────────────────────────────────────────
# Write output
# ────────────────────────────────────────────────────────────────────────────
open('/tmp/app_hotfix.py', 'w', encoding='utf-8').write(src)
new_len = len(src)
print(f"\nDone. Original: {original_len} bytes → Hotfix: {new_len} bytes (delta: {new_len - original_len:+d})")
