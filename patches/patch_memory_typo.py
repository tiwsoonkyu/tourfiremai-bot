"""P4 Patch — Conversation Memory After Delay + Typo Country Detection
Patches:
  1. get_context(): refresh Redis TTL on every read
  2. normalize_country_typo(): alias/typo lookup table for 8 countries
  3. process_message(): pre-classifier country fill (no LLM confirmation)
  4. process_message(): post-classifier override — action=search, preserve budget
"""
import sys

with open('/tmp/app_budget_patched.py', 'r', encoding='utf-8') as f:
    content = f.read()

orig_len = len(content)
print(f"Original: {orig_len} bytes, {content.count(chr(10))+1} lines")

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 1: get_context() — refresh Redis TTL on every successful read
# ─────────────────────────────────────────────────────────────────────────────
OLD_GET_CTX = (
    'def get_context(psid: str) -> dict:\n'
    '    """คืน structured context ของลูกค้า"""\n'
    '    if _redis:\n'
    '        try:\n'
    '            raw = _redis.get(_ctx_key(psid))\n'
    '            if raw:\n'
    '                return json.loads(raw)\n'
    '        except Exception as e:\n'
    '            logger.warning(f"Redis ctx get error: {e}")\n'
    '\n'
    '    return dict(_ctx_store.get(psid, _EMPTY_CTX))\n'
)

NEW_GET_CTX = (
    'def get_context(psid: str) -> dict:\n'
    '    """คืน structured context ของลูกค้า"""\n'
    '    if _redis:\n'
    '        try:\n'
    '            raw = _redis.get(_ctx_key(psid))\n'
    '            if raw:\n'
    '                _redis.expire(_ctx_key(psid), REDIS_TTL_SEC)  # refresh TTL on every read\n'
    '                return json.loads(raw)\n'
    '        except Exception as e:\n'
    '            logger.warning(f"Redis ctx get error: {e}")\n'
    '\n'
    '    return dict(_ctx_store.get(psid, _EMPTY_CTX))\n'
)

if OLD_GET_CTX in content:
    content = content.replace(OLD_GET_CTX, NEW_GET_CTX, 1)
    print("✅ Patch 1: Redis TTL refreshed on every get_context() read")
else:
    print("❌ Patch 1 FAILED — anchor not found")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 2: Add _COUNTRY_ALIAS + normalize_country_typo() after detect_budget_type()
# ─────────────────────────────────────────────────────────────────────────────
OLD_AFTER_DETECT = (
    'def select_budget_tiers(tours: list, budget: int, budget_type: str) -> list:\n'
)

NEW_AFTER_DETECT = (
    '# ── Country alias / typo map ─────────────────────────────────────────────────\n'
    '# key: lowercase alias/typo → value: (canonical_thai_name, country_id_str)\n'
    '_COUNTRY_ALIAS: dict = {\n'
    '    # ญี่ปุ่น\n'
    '    "ญี่ปุ่น": ("ญี่ปุ่น", "2"), "ญี่ปุ่ร": ("ญี่ปุ่น", "2"),\n'
    '    "ญี่ป่น": ("ญี่ปุ่น", "2"), "ญีปุ่น": ("ญี่ปุ่น", "2"),\n'
    '    "ญีปุน": ("ญี่ปุ่น", "2"), "ญี่ปุน": ("ญี่ปุ่น", "2"),\n'
    '    "japan": ("ญี่ปุ่น", "2"), "jp": ("ญี่ปุ่น", "2"), "japon": ("ญี่ปุ่น", "2"),\n'
    '    # เกาหลี\n'
    '    "เกาหลี": ("เกาหลี", "1"), "เกาหรี": ("เกาหลี", "1"),\n'
    '    "เกาหลีใต้": ("เกาหลี", "1"), "เกาหลีเหนือ": ("เกาหลี", "1"),\n'
    '    "korea": ("เกาหลี", "1"), "south korea": ("เกาหลี", "1"), "kr": ("เกาหลี", "1"),\n'
    '    # จีน\n'
    '    "จีน": ("จีน", "5"), "ประเทศจีน": ("จีน", "5"),\n'
    '    "china": ("จีน", "5"), "cn": ("จีน", "5"),\n'
    '    # ไต้หวัน\n'
    '    "ไต้หวัน": ("ไต้หวัน", "19"), "ไต้หวน": ("ไต้หวัน", "19"),\n'
    '    "ไต้หวาน": ("ไต้หวัน", "19"), "ใต้หวัน": ("ไต้หวัน", "19"),\n'
    '    "taiwan": ("ไต้หวัน", "19"), "tw": ("ไต้หวัน", "19"),\n'
    '    # เวียดนาม\n'
    '    "เวียดนาม": ("เวียดนาม", "7"), "เวียตนาม": ("เวียดนาม", "7"),\n'
    '    "เวียดนาน": ("เวียดนาม", "7"),\n'
    '    "vietnam": ("เวียดนาม", "7"), "viet nam": ("เวียดนาม", "7"), "vn": ("เวียดนาม", "7"),\n'
    '    # ฮ่องกง\n'
    '    "ฮ่องกง": ("ฮ่องกง", "3"), "ฮองกง": ("ฮ่องกง", "3"),\n'
    '    "ฮ่องกงค่ะ": ("ฮ่องกง", "3"),\n'
    '    "hongkong": ("ฮ่องกง", "3"), "hong kong": ("ฮ่องกง", "3"), "hk": ("ฮ่องกง", "3"),\n'
    '    # สิงคโปร์\n'
    '    "สิงคโปร์": ("สิงคโปร์", "4"), "สิงค์โปร์": ("สิงคโปร์", "4"),\n'
    '    "สิงคโปร": ("สิงคโปร์", "4"),\n'
    '    "singapore": ("สิงคโปร์", "4"), "sg": ("สิงคโปร์", "4"),\n'
    '    # มาเลเซีย\n'
    '    "มาเลเซีย": ("มาเลเซีย", "6"), "มาเลย์": ("มาเลเซีย", "6"),\n'
    '    "มาเลเซีย": ("มาเลเซีย", "6"),\n'
    '    "malaysia": ("มาเลเซีย", "6"), "my": ("มาเลเซีย", "6"),\n'
    '}\n'
    '# polite particle suffixes to strip before lookup\n'
    '_TH_POLITE = ["ครับ", "ค่ะ", "คะ", "นะครับ", "นะคะ", "จ้า", "จ้าย", "นะ"]\n'
    '\n'
    '\n'
    'def normalize_country_typo(text: str):\n'
    '    """ตรวจว่าข้อความเป็นชื่อประเทศ (รองรับ typo + alias + อังกฤษ)\n'
    '    คืน (canonical_name: str, country_id: str) หรือ (None, None)\n'
    '    ใช้ก่อน decide_action() เพื่อ bypass LLM confirmation\n'
    '    """\n'
    '    t = text.strip().lower()\n'
    '    # Strip polite particles\n'
    '    for sfx in _TH_POLITE:\n'
    '        if t.endswith(sfx):\n'
    '            t = t[:-len(sfx)].strip()\n'
    '            break\n'
    '    # Exact match\n'
    '    if t in _COUNTRY_ALIAS:\n'
    '        return _COUNTRY_ALIAS[t]\n'
    '    # Without spaces (e.g. "hong kong" → "hongkong")\n'
    '    t_nospace = t.replace(" ", "")\n'
    '    if t_nospace in _COUNTRY_ALIAS:\n'
    '        return _COUNTRY_ALIAS[t_nospace]\n'
    '    # Substring match — only for short messages (pure country reply)\n'
    '    if len(t) <= 20:\n'
    '        for alias, val in _COUNTRY_ALIAS.items():\n'
    '            if len(alias) >= 2 and alias in t:\n'
    '                return val\n'
    '    return None, None\n'
    '\n'
    '\n'
    'def select_budget_tiers(tours: list, budget: int, budget_type: str) -> list:\n'
)

if OLD_AFTER_DETECT in content:
    content = content.replace(OLD_AFTER_DETECT, NEW_AFTER_DETECT, 1)
    print("✅ Patch 2: _COUNTRY_ALIAS + normalize_country_typo() added")
else:
    print("❌ Patch 2 FAILED — anchor not found")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 3 + 4: process_message() — country typo pre-fill + post-classifier override
# Insert BEFORE decide_action() call and AFTER extraction block
# ─────────────────────────────────────────────────────────────────────────────
OLD_PREACTION = (
    '        # ── Rule-based option selection (fast, before Claude call) ─────\n'
    '        _rule_option_idx = parse_option_index_rule_based(text, _last_opts_count) if _last_opts_count > 0 else None\n'
    '        if _rule_option_idx:\n'
    '            logger.info(f"[OPTION_SELECT] rule-based detected: index={_rule_option_idx} text={text[:40]!r}")\n'
    '        action_data = decide_action(text, history, last_options_count=_last_opts_count)\n'
    '        action               = action_data.get("action", "reply")\n'
    '        country_id           = action_data.get("country_id")\n'
    '        selected_option_idx  = action_data.get("selected_option_index")\n'
    '        uses_previous        = action_data.get("uses_previous_option", False)\n'
    '        # Rule-based override: ถ้า Claude ไม่ detect แต่ rule-based ตรวจเจอ\n'
    '        if _rule_option_idx and not selected_option_idx:\n'
    '            selected_option_idx = _rule_option_idx\n'
    '            uses_previous = True\n'
    '            logger.info(f"[OPTION_SELECT] rule-based override applied: index={_rule_option_idx}")\n'
    '        clear_prev_options   = action_data.get("clear_previous_options", False)\n'
    '        lead_stage           = action_data.get("lead_stage", "cold")\n'
    '        should_search        = action_data.get("should_search", True)\n'
    '        missing_field_to_ask = action_data.get("missing_field_to_ask", None)\n'
    '        city_hint            = action_data.get("city") or action_data.get("city_hint")\n'
    '        classifier_month     = action_data.get("month")\n'
    '        classifier_budget    = action_data.get("budget_per_person")\n'
    '        classifier_pax       = action_data.get("pax")\n'
    '        classifier_country   = action_data.get("country_name")\n'
    '        logger.info(f"Action: {action}, country_id: {country_id}, city: {city_hint}, lead_stage: {lead_stage}, "\n'
    '                    f"selected_idx: {selected_option_idx}, uses_prev: {uses_previous}, clear_prev: {clear_prev_options}")\n'
)

NEW_PREACTION = (
    '        # ── Rule-based option selection (fast, before Claude call) ─────\n'
    '        _rule_option_idx = parse_option_index_rule_based(text, _last_opts_count) if _last_opts_count > 0 else None\n'
    '        if _rule_option_idx:\n'
    '            logger.info(f"[OPTION_SELECT] rule-based detected: index={_rule_option_idx} text={text[:40]!r}")\n'
    '\n'
    '        # ── Country typo pre-fill (before LLM, no confirmation needed) ──\n'
    '        # ถ้า user ตอบชื่อประเทศ (อาจ typo) และ ctx ยังไม่รู้ประเทศ → fill ทันที\n'
    '        _norm_cname, _norm_cid = normalize_country_typo(text)\n'
    '        _ctx_has_country = bool(ctx.get("country_id") or ctx.get("country"))\n'
    '        _ctx_has_prior   = bool(ctx.get("budget_per_person") or ctx.get("last_options")\n'
    '                                 or ctx.get("selected_tour") or ctx.get("city_hint")\n'
    '                                 or ctx.get("month") or ctx.get("pax"))\n'
    '        _direct_country_fill = False\n'
    '        if _norm_cname and not _ctx_has_country:\n'
    '            # Fill when: have prior context OR short message (pure country reply)\n'
    '            if _ctx_has_prior or len(text.strip()) <= 20:\n'
    '                ctx["country_id"]   = _norm_cid\n'
    '                ctx["country"]      = _norm_cname\n'
    '                ctx["country_name"] = _norm_cname\n'
    '                _direct_country_fill = True\n'
    '                logger.info(f"[COUNTRY_NORM] \'{text[:20]}\' → \'{_norm_cname}\' (id={_norm_cid}) budget={ctx.get(\'budget_per_person\')}")\n'
    '\n'
    '        action_data = decide_action(text, history, last_options_count=_last_opts_count)\n'
    '        action               = action_data.get("action", "reply")\n'
    '        country_id           = action_data.get("country_id")\n'
    '        selected_option_idx  = action_data.get("selected_option_index")\n'
    '        uses_previous        = action_data.get("uses_previous_option", False)\n'
    '        # Rule-based override: ถ้า Claude ไม่ detect แต่ rule-based ตรวจเจอ\n'
    '        if _rule_option_idx and not selected_option_idx:\n'
    '            selected_option_idx = _rule_option_idx\n'
    '            uses_previous = True\n'
    '            logger.info(f"[OPTION_SELECT] rule-based override applied: index={_rule_option_idx}")\n'
    '        clear_prev_options   = action_data.get("clear_previous_options", False)\n'
    '        lead_stage           = action_data.get("lead_stage", "cold")\n'
    '        should_search        = action_data.get("should_search", True)\n'
    '        missing_field_to_ask = action_data.get("missing_field_to_ask", None)\n'
    '        city_hint            = action_data.get("city") or action_data.get("city_hint")\n'
    '        classifier_month     = action_data.get("month")\n'
    '        classifier_budget    = action_data.get("budget_per_person")\n'
    '        classifier_pax       = action_data.get("pax")\n'
    '        classifier_country   = action_data.get("country_name")\n'
    '        logger.info(f"Action: {action}, country_id: {country_id}, city: {city_hint}, lead_stage: {lead_stage}, "\n'
    '                    f"selected_idx: {selected_option_idx}, uses_prev: {uses_previous}, clear_prev: {clear_prev_options}")\n'
    '\n'
    '        # ── Country typo override: bypass LLM action if we filled country ─\n'
    '        if _direct_country_fill:\n'
    '            # Force search — do NOT ask for confirmation\n'
    '            if not country_id:\n'
    '                country_id = _norm_cid\n'
    '            action = "search"\n'
    '            should_search = True\n'
    '            missing_field_to_ask = None\n'
    '            # IMPORTANT: never wipe budget when filling country\n'
    '            clear_prev_options = False\n'
    '            logger.info(f"[COUNTRY_NORM] Override → action=search country_id={country_id} budget={ctx.get(\'budget_per_person\')} clear_prev=False")\n'
)

if OLD_PREACTION in content:
    content = content.replace(OLD_PREACTION, NEW_PREACTION, 1)
    print("✅ Patch 3+4: country typo pre-fill + LLM override injected")
else:
    print("❌ Patch 3+4 FAILED — anchor not found")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Write result
# ─────────────────────────────────────────────────────────────────────────────
with open('/tmp/app_memory_patched.py', 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\nPatched: {len(content)} bytes, {content.count(chr(10))+1} lines")
print(f"Delta: {len(content) - orig_len:+d} bytes")
