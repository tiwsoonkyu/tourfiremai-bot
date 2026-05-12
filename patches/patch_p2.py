"""P2 Patch — Select tour by price / web_code / tour_code / name keyword"""
import sys

with open('/tmp/app_p2.py', 'r', encoding='utf-8') as f:
    content = f.read()

orig_len = len(content)
print(f"Original: {orig_len} bytes, {content.count(chr(10))+1} lines")

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 1: Add resolve_selected_tour_from_text() function
# Placed after is_change_tour_request() at line ~912
# ─────────────────────────────────────────────────────────────────────────────
ANCHOR_AFTER = (
    'def is_change_tour_request(text: str) -> bool:\n'
    '    """True ถ้า user ต้องการดูโปรแกรมอื่น (unlock booking lock + search new)"""\n'
    '    return any(k in text for k in _CHANGE_TOUR_KEYWORDS)\n'
)

NEW_FUNCTION = (
    'def is_change_tour_request(text: str) -> bool:\n'
    '    """True ถ้า user ต้องการดูโปรแกรมอื่น (unlock booking lock + search new)"""\n'
    '    return any(k in text for k in _CHANGE_TOUR_KEYWORDS)\n'
    '\n'
    '\n'
    'def resolve_selected_tour_from_text(text: str, last_options: list) -> dict:\n'
    '    """ค้นหา selected_tour จาก text โดยไม่ต้องใช้ option index\n'
    '    Priority: web_code > tour_code_real > exact price (1 match) > name/city keyword\n'
    '    คืน {"tour": <tour_dict>, "method": str, "ambiguous": False}\n'
    '       หรือ {"ambiguous": True, "matches": [tour,...], "price": int} สำหรับ price ที่มีหลายตัว\n'
    '       หรือ {} ถ้าไม่เจอ\n'
    '    """\n'
    '    import re as _re\n'
    '    if not last_options or not isinstance(last_options, list):\n'
    '        return {}\n'
    '    text_up = text.upper().strip()\n'
    '\n'
    '    # Priority 1: exact web_code match e.g. "ap242807"\n'
    '    for t in last_options:\n'
    '        wc = (t.get("web_code") or t.get("tour_code") or "").strip().upper()\n'
    '        if wc and wc in text_up:\n'
    '            return {"tour": t, "method": "web_code"}\n'
    '\n'
    '    # Priority 2: exact tour_code_real match e.g. "VZ-TPE07-2"\n'
    '    for t in last_options:\n'
    '        rc = (t.get("tour_code_real") or "").strip().upper()\n'
    '        if rc and len(rc) >= 4 and rc in text_up:\n'
    '            return {"tour": t, "method": "tour_code_real"}\n'
    '\n'
    '    # Priority 3: price match — extract all numeric sequences from text\n'
    '    # support "8999", "8,999", "8.999"\n'
    '    raw_nums = _re.findall(r"\\d[\\d,\\.]*\\d|\\d", text)\n'
    '    prices_in_text = set()\n'
    '    for n in raw_nums:\n'
    '        cleaned = n.replace(",", "").replace(".", "")\n'
    '        if cleaned.isdigit() and 2000 <= int(cleaned) <= 500000:\n'
    '            prices_in_text.add(int(cleaned))\n'
    '    if prices_in_text:\n'
    '        for price_val in prices_in_text:\n'
    '            matches = []\n'
    '            for t in last_options:\n'
    '                pm = t.get("price_min") or t.get("price")\n'
    '                try:\n'
    '                    if pm and int(str(pm).replace(",", "").replace(" ", "")) == price_val:\n'
    '                        matches.append(t)\n'
    '                except (ValueError, TypeError):\n'
    '                    pass\n'
    '            if len(matches) == 1:\n'
    '                return {"tour": matches[0], "method": "price", "price": price_val}\n'
    '            if len(matches) > 1:\n'
    '                return {"ambiguous": True, "matches": matches, "price": price_val}\n'
    '\n'
    '    # Priority 4: name / city keyword — tokenize tour name and match\n'
    '    # Use 3-char minimum to avoid false positives\n'
    '    _TH_STOPWORDS = {"ทัวร์", "โปร", "ราคา", "บาท", "วัน", "คืน", "เที่ยว",\n'
    '                     "ครั้ง", "ท่าน", "คน", "พัก", "บิน", "สาย", "การ"}\n'
    '    for t in last_options:\n'
    '        name = t.get("name") or ""\n'
    '        # extract meaningful words (Thai 3+ chars or English 4+ chars)\n'
    '        th_words = [w for w in name.split() if len(w) >= 3 and w not in _TH_STOPWORDS]\n'
    '        for w in th_words:\n'
    '            if w.upper() in text_up or w in text:\n'
    '                return {"tour": t, "method": "name_keyword", "keyword": w}\n'
    '\n'
    '    return {}\n'
)

if ANCHOR_AFTER in content:
    content = content.replace(ANCHOR_AFTER, NEW_FUNCTION, 1)
    print("✅ Patch 1: resolve_selected_tour_from_text() added")
else:
    print("❌ Patch 1 FAILED — anchor not found")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 2: Call resolver in process_message() — inject BEFORE option-index block
# We insert after CODE_LOCK block ends and before the "Resolve selected_option_index" block
# ─────────────────────────────────────────────────────────────────────────────
OLD_OPTION_INDEX_BLOCK = (
    '        # ── Resolve selected_option_index → set selected_tour ────────────\n'
    '        if uses_previous and selected_option_idx and ctx.get("last_options"):\n'
)

NEW_OPTION_INDEX_BLOCK = (
    '        # ── Resolve by price/code/name (before index resolver) ─────────\n'
    '        # ลูกค้าพิมพ์ "สนใจตัว 8999" / "ap242807" / "VZ-TPE07-2" / ชื่อเมือง\n'
    '        if not ctx.get("selected_tour") and ctx.get("last_options"):\n'
    '            _lopt2 = ctx.get("last_options")\n'
    '            if isinstance(_lopt2, str):\n'
    '                try:\n'
    '                    import json as _json2; _lopt2 = _json2.loads(_lopt2)\n'
    '                except Exception:\n'
    '                    _lopt2 = []\n'
    '            if isinstance(_lopt2, list) and len(_lopt2) > 0:\n'
    '                _resolve = resolve_selected_tour_from_text(text, _lopt2)\n'
    '                if _resolve.get("ambiguous"):\n'
    '                    # หลายตัวราคาเดียวกัน → ถามยืนยัน\n'
    '                    _amb_matches = _resolve.get("matches", [])\n'
    '                    _amb_price   = _resolve.get("price", 0)\n'
    '                    _amb_count   = len(_amb_matches)\n'
    '                    _amb_nums    = " หรือ ".join(\n'
    '                        f"ตัวที่ {_lopt2.index(m)+1}" for m in _amb_matches if m in _lopt2\n'
    '                    ) or " หรือ ".join(\n'
    '                        f"ตัวที่ {i+1}" for i, m in enumerate(_lopt2) if m in _amb_matches\n'
    '                    )\n'
    '                    _amb_msg = (\n'
    '                        f"ราคา {_amb_price:,} บาท มี {_amb_count} โปรแกรมค่ะ "\n'
    '                        f"หมายถึง{_amb_nums}คะ? \U0001f60a"\n'
    '                    )\n'
    '                    send_message(sender_id, _amb_msg)\n'
    '                    save_to_history(sender_id, "user", text)\n'
    '                    save_to_history(sender_id, "assistant", _amb_msg)\n'
    '                    log_chat_event(sender_id, "price_ambiguous", text, action, lead_stage, ctx)\n'
    '                    return jsonify({"status": "ok"}), 200\n'
    '                elif _resolve.get("tour"):\n'
    '                    _rt = _resolve["tour"]\n'
    '                    _method = _resolve.get("method", "?")\n'
    '                    ctx["selected_tour"]           = _rt\n'
    '                    ctx["selected_tour_name"]      = _rt.get("name", "")\n'
    '                    ctx["selected_tour_url"]       = _rt.get("url", _rt.get("link", ""))\n'
    '                    ctx["selected_tour_code"]      = _rt.get("tour_code_real", "") or _rt.get("tour_code", "") or ""\n'
    '                    ctx["selected_tour_web_code"]  = _rt.get("web_code", "") or _rt.get("tour_code", "") or ""\n'
    '                    ctx["selected_tour_airline"]   = _rt.get("airline", "") or ""\n'
    '                    ctx["booking_context_locked"]  = True\n'
    '                    ctx["pre_booking_detail_sent"] = False\n'
    '                    if not ctx.get("booking_fields"):\n'
    '                        ctx["booking_fields"] = {}\n'
    '                    ctx["pending_action"] = "collect_booking_info"\n'
    '                    if lead_stage not in ("hot", "booking", "paid"):\n'
    '                        lead_stage = "hot"\n'
    '                        ctx["_lead_stage"] = "hot"\n'
    '                    logger.info(f"[SMART_SELECT] method={_method} → {ctx[\'selected_tour_name\']} [{ctx[\'selected_tour_web_code\']}]")\n'
    '                    # Build confirmation reply\n'
    '                    _st_name  = ctx["selected_tour_name"]\n'
    '                    _st_code  = ctx["selected_tour_code"]\n'
    '                    _st_wc    = ctx["selected_tour_web_code"]\n'
    '                    _st_price = _rt.get("price_min") or _rt.get("price", "")\n'
    '                    _st_dep   = _rt.get("departure_dates", "")\n'
    '                    _cname    = ctx.get("customer_name") or ""\n'
    '                    _name_str = f"คุณ {_cname} " if _cname else ""\n'
    '                    def _fmt_p(v):\n'
    '                        try: return f"{int(v):,}" if v else ""\n'
    '                        except: return str(v) if v else ""\n'
    '                    _confirm_lines = [\n'
    '                        f"ได้เลยค่ะ {_name_str}\U0001f60a",\n'
    '                    ]\n'
    '                    if _method == "price":\n'
    '                        _pv = _resolve.get("price", 0)\n'
    '                        _confirm_lines.append(f"สนใจตัวราคา {_pv:,} บาทนี้ใช่ไหมคะ")\n'
    '                    _confirm_lines += [\n'
    '                        "",\n'
    '                        f"✈️ {_st_name}",\n'
    '                        f"\U0001f3f7 รหัสทัวร์: {_st_code}" if _st_code else "",\n'
    '                        f"\U0001f511 รหัสเว็บ: {_st_wc}" if _st_wc else "",\n'
    '                        f"\U0001f4b0 ราคาเริ่ม: {_fmt_p(_st_price)} บาท" if _st_price else "",\n'
    '                        f"\U0001f4c5 วันเดินทาง: {_st_dep}" if _st_dep else "",\n'
    '                        "",\n'
    '                        "สนใจเดินทางวันไหนคะ? \U0001f60a",\n'
    '                    ]\n'
    '                    _confirm_msg = "\\n".join(l for l in _confirm_lines if l is not None)\n'
    '                    save_context(sender_id, ctx)\n'
    '                    send_message(sender_id, _confirm_msg)\n'
    '                    save_to_history(sender_id, "user", text)\n'
    '                    save_to_history(sender_id, "assistant", _confirm_msg)\n'
    '                    log_chat_event(sender_id, "smart_select", text, action, lead_stage, ctx)\n'
    '                    return jsonify({"status": "ok"}), 200\n'
    '\n'
    '        # ── Resolve selected_option_index → set selected_tour ────────────\n'
    '        if uses_previous and selected_option_idx and ctx.get("last_options"):\n'
)

if OLD_OPTION_INDEX_BLOCK in content:
    content = content.replace(OLD_OPTION_INDEX_BLOCK, NEW_OPTION_INDEX_BLOCK, 1)
    print("✅ Patch 2: Smart resolver injected in process_message()")
else:
    print("❌ Patch 2 FAILED — anchor not found")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Write result
# ─────────────────────────────────────────────────────────────────────────────
with open('/tmp/app_p2_patched.py', 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\nPatched: {len(content)} bytes, {content.count(chr(10))+1} lines")
print(f"Delta: {len(content) - orig_len:+d} bytes")
