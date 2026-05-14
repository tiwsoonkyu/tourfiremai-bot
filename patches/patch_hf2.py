import re

src = open('/tmp/app_hf2.py', encoding='utf-8').read()
original_len = len(src.split('\n'))
print(f"Original lines: {original_len}")

# ─────────────────────────────────────────────────────────────────────────────
# FIX A: Relax the atomic save abort condition (search path)
# Only abort if Redis save failed — Supabase failure alone should not stop bot
# ─────────────────────────────────────────────────────────────────────────────

OLD_A = """                        if not _snap_redis_ok and not _snap_supa_ok:
                            logger.error("OFFER_SNAPSHOT_SAVE_FAIL_BOTH psid=%s offer_set_id=%s",
                                         sender_id, _snap_offer_set_id)
                            _save_fail_msg = "ขออภัยค่ะ ระบบบันทึกรายการทัวร์ขัดข้องชั่วคราว เดี๋ยวให้ทีมงานช่วยเช็กให้นะคะ"
                            send_message(sender_id, _save_fail_msg)
                            save_to_history(sender_id, "assistant", _save_fail_msg)
                            save_context(sender_id, ctx)
                            return
                        elif not _snap_redis_ok:
                            logger.warning("OFFER_SNAPSHOT_REDIS_FAIL_ONLY psid=%s — continuing with Supabase only",
                                           sender_id)"""

NEW_A = """                        if not _snap_redis_ok:
                            # Redis save failed — try once more before giving up
                            try:
                                import uuid as _uuid_retry
                                _retry_snapshot = {
                                    "psid": sender_id,
                                    "offer_set_id": _snap_offer_set_id,
                                    "created_at": datetime.utcnow().isoformat() + "Z",
                                    "expires_at": (datetime.utcnow() + timedelta(days=30)).isoformat() + "Z",
                                    "search_context": _snap_search_ctx,
                                    "options": [dict(t, **{"_offer_index": i+1}) for i, t in enumerate(tour_meta[:3])],
                                }
                                _redis.setex(
                                    _offer_snapshot_redis_key(sender_id),
                                    REDIS_TTL_SEC,
                                    json.dumps(_retry_snapshot, ensure_ascii=False),
                                )
                                _snap_redis_ok = True
                                logger.info("OFFER_SNAPSHOT_REDIS_RETRY_OK psid=%s", sender_id)
                            except Exception as _retry_e:
                                logger.error("OFFER_SNAPSHOT_REDIS_RETRY_FAIL psid=%s error=%s", sender_id, _retry_e)
                            if not _snap_redis_ok:
                                # Redis completely unavailable — still serve tours, log critical
                                logger.error("OFFER_SNAPSHOT_REDIS_FAIL_CRITICAL psid=%s — serving tours without snapshot", sender_id)
                                # Do NOT abort — better to show tours than error message
                        elif not _snap_supa_ok:
                            logger.warning("OFFER_SNAPSHOT_SUPABASE_ONLY_FAIL psid=%s — Redis OK, Supabase table may not exist yet", sender_id)
                            # Continue normally — Redis snapshot is sufficient for selection"""

if OLD_A in src:
    src = src.replace(OLD_A, NEW_A, 1)
    print("✅ FIX A (search path abort → relax) applied")
else:
    print("❌ FIX A anchor NOT FOUND — searching for partial match...")
    if "_save_fail_msg" in src:
        print("   _save_fail_msg found in file — check indentation/whitespace")
    if "OFFER_SNAPSHOT_SAVE_FAIL_BOTH" in src:
        print("   OFFER_SNAPSHOT_SAVE_FAIL_BOTH found")


# ─────────────────────────────────────────────────────────────────────────────
# FIX B1: Fix TG airline patterns — add word-adjacent Thai patterns
# ─────────────────────────────────────────────────────────────────────────────

OLD_B1 = """        (["การบินไทย", "thai airways", "thai air", " tg ", "สายการบินไทย", "tg "], "TG", "full_service"),"""

NEW_B1 = """        (["การบินไทย", "บินการบินไทย", "ไทยแอร์เวย์", "thai airways", "thai air", " tg ", "สายการบินไทย", "tg ", "ทีจี", "อยากบิน tg"], "TG", "full_service"),"""

if OLD_B1 in src:
    src = src.replace(OLD_B1, NEW_B1, 1)
    print("✅ FIX B1 (TG airline patterns) applied")
else:
    print("❌ FIX B1 anchor NOT FOUND")

# ─────────────────────────────────────────────────────────────────────────────
# FIX B2: Change pattern matching to check both _t_padded AND plain t
# ─────────────────────────────────────────────────────────────────────────────

OLD_B2 = """    _t_padded = f" {t} "  # pad for word boundary matching
    for _patterns, _code, _atype in _airline_map:
        if any(p in _t_padded for p in _patterns):"""

NEW_B2 = """    _t_padded = f" {t} "  # pad for word boundary matching
    for _patterns, _code, _atype in _airline_map:
        # Check both padded (space-separated) and plain (Thai word-adjacent, no spaces)
        if any((p in _t_padded or p in t) for p in _patterns):"""

if OLD_B2 in src:
    src = src.replace(OLD_B2, NEW_B2, 1)
    print("✅ FIX B2 (dual airline pattern check) applied")
else:
    print("❌ FIX B2 anchor NOT FOUND")

# ─────────────────────────────────────────────────────────────────────────────
# FIX C: Strengthen _skip_llm_classify: also trigger when airline just detected + country known
# ─────────────────────────────────────────────────────────────────────────────

OLD_C = """        _skip_llm_classify = False
        if ctx.get("request_type") in ("upgrade_options", "downgrade_options", "airline_filter") \
                and ctx.get("country_id"):
            action = "search"
            _skip_llm_classify = True
            logger.info("INTENT_OVERRIDE action=search request_type=%s country_id=%s",
                        ctx.get("request_type"), ctx.get("country_id"))"""

NEW_C = """        _skip_llm_classify = False
        _should_skip = (
            # Explicit upgrade/downgrade/airline request with known country
            (ctx.get("request_type") in ("upgrade_options", "downgrade_options", "airline_filter")
             and ctx.get("country_id"))
            or
            # Airline just detected + country known (even if request_type not explicitly set)
            (_intent_mods.get("airline_preference") and ctx.get("country_id"))
        )
        if _should_skip:
            action = "search"
            _skip_llm_classify = True
            logger.info("INTENT_OVERRIDE action=search reason=request_type=%s airline=%s country=%s",
                        ctx.get("request_type"), ctx.get("airline_preference"), ctx.get("country_id"))"""

if OLD_C in src:
    src = src.replace(OLD_C, NEW_C, 1)
    print("✅ FIX C (_should_skip + airline detection) applied")
else:
    print("❌ FIX C anchor NOT FOUND — checking whitespace...")
    if "_skip_llm_classify = False" in src:
        print("   _skip_llm_classify = False exists")
    if "upgrade_options" in src:
        print("   upgrade_options exists")

# ─────────────────────────────────────────────────────────────────────────────
# FIX D1: Broaden country retention guard to catch more action names
# ─────────────────────────────────────────────────────────────────────────────

OLD_D1 = """        if action in ("ask_country", "ASK_COUNTRY", "ask_destination", "reply") \
                and missing_field_to_ask == "country" \
                and ctx.get("country_id"):
            logger.info("COUNTRY_RETENTION: already know country_id=%s, overriding action to search", ctx.get("country_id"))
            action = "search"
            should_search = True
            missing_field_to_ask = None
            if not country_id:
                country_id = str(ctx.get("country_id", ""))"""

NEW_D1 = """        if action in ("ask_country", "ASK_COUNTRY", "ask_destination", "clarify_country",
                      "clarify", "ask", "reply") \
                and missing_field_to_ask == "country" \
                and ctx.get("country_id"):
            logger.info("COUNTRY_RETENTION: already know country_id=%s, overriding action to search", ctx.get("country_id"))
            action = "search"
            should_search = True
            missing_field_to_ask = None
            if not country_id:
                country_id = str(ctx.get("country_id", ""))"""

if OLD_D1 in src:
    src = src.replace(OLD_D1, NEW_D1, 1)
    print("✅ FIX D1 (broaden country retention guard) applied")
else:
    print("❌ FIX D1 anchor NOT FOUND")
    # Try alternative whitespace form
    OLD_D1_ALT = 'if action in ("ask_country", "ASK_COUNTRY", "ask_destination", "reply")                 and missing_field_to_ask == "country"                 and ctx.get("country_id"):'
    if OLD_D1_ALT in src:
        print("   Found inline form (no backslash continuation)")

# ─────────────────────────────────────────────────────────────────────────────
# FIX D2: Add general_chat + airline + country override
# Insert after the country retention guard block
# ─────────────────────────────────────────────────────────────────────────────

OLD_D2 = """        # ── SOONEST OVERRIDE — rule-based safety net ─────────────────────────"""

NEW_D2 = """        # ── General chat + airline + known country → force search ─────────────
        if action in ("general_chat", "chat", "general", "reply") \
                and ctx.get("country_id") \
                and ctx.get("airline_preference"):
            action = "search"
            should_search = True
            _skip_llm_classify = True
            logger.info("COUNTRY_RETENTION_AIRLINE_OVERRIDE action=search country=%s airline=%s",
                        ctx.get("country_id"), ctx.get("airline_preference"))
        # ──────────────────────────────────────────────────────────────────────

        # ── SOONEST OVERRIDE — rule-based safety net ─────────────────────────"""

if OLD_D2 in src:
    src = src.replace(OLD_D2, NEW_D2, 1)
    print("✅ FIX D2 (COUNTRY_RETENTION_AIRLINE_OVERRIDE) applied")
else:
    print("❌ FIX D2 anchor NOT FOUND")

# Write output
open('/tmp/app_hf2_fixed.py', 'w', encoding='utf-8').write(src)
new_len = len(src.split('\n'))
print(f"\nNew lines: {new_len} (added {new_len - original_len})")
print("Written to /tmp/app_hf2_fixed.py")
