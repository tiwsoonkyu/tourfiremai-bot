"""P1 Patch Script — Pre-booking Detail + Fee Memory Fix"""
import re, sys

with open('/tmp/app_p1.py', 'r', encoding='utf-8') as f:
    content = f.read()

original_len = len(content)
print(f"Original: {original_len} bytes, {content.count(chr(10))+1} lines")

# ─────────────────────────────────────────────────────────────────────────────
# PATCH A-1: Move _FEE_KW_EARLY before clear_previous_options and guard the block
# ─────────────────────────────────────────────────────────────────────────────
OLD_CLEAR = (
    '        # ── clear_previous_options: ลูกค้าเปลี่ยนประเทศ ──────────────────\n'
    '        if clear_prev_options:\n'
    '            ctx["last_options"] = []\n'
    '            ctx["selected_tour"] = None\n'
    '            ctx["selected_tour_name"] = None\n'
    '            ctx["selected_tour_url"] = None\n'
    '            ctx["city_hint"] = None\n'
    '            ctx["booking_context_locked"] = False\n'
    '            ctx["booking_fields"] = {}\n'
    '            logger.info(f"\U0001f504 clear_previous_options triggered for {sender_id}")\n'
    '        save_context(sender_id, ctx)'
)
NEW_CLEAR = (
    '        # ── Pre-check: fee keywords early (used by clear guard below) ──────\n'
    '        _FEE_KW_EARLY = {"ค่าทิป", "ทิปไกด์", "ทิปคนขับ", "มัดจำ", "ค่ามัดจำ",\n'
    '                         "วีซ่า", "ค่าวีซ่า", "พักเดี่ยว", "ค่าพักเดี่ยว",\n'
    '                         "จ่ายจริง", "ค่าใช้จ่าย", "ราคาจริง",\n'
    '                         "ราคารวมทิป", "รวมทิป", "ยอดจ่ายจริง", "ทิปรวม"}\n'
    '        _is_fee_q_early = any(k in text for k in _FEE_KW_EARLY)\n'
    '\n'
    '        # ── clear_previous_options: ลูกค้าเปลี่ยนประเทศ ──────────────────\n'
    '        # ⚠️ ถ้าเป็น fee question ห้าม clear selected_tour — ลูกค้าถามต่อเนื่อง\n'
    '        if clear_prev_options and not (_is_fee_q_early and ctx.get("selected_tour")):\n'
    '            ctx["last_options"] = []\n'
    '            ctx["selected_tour"] = None\n'
    '            ctx["selected_tour_name"] = None\n'
    '            ctx["selected_tour_url"] = None\n'
    '            ctx["city_hint"] = None\n'
    '            ctx["booking_context_locked"] = False\n'
    '            ctx["booking_fields"] = {}\n'
    '            logger.info(f"\U0001f504 clear_previous_options triggered for {sender_id}")\n'
    '        save_context(sender_id, ctx)'
)

if OLD_CLEAR in content:
    content = content.replace(OLD_CLEAR, NEW_CLEAR, 1)
    print("✅ Patch A-1: clear_previous_options guard applied")
else:
    print("❌ Patch A-1 FAILED — token mismatch")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# PATCH A-2: Remove old _FEE_KW_EARLY block (moved above)
# ─────────────────────────────────────────────────────────────────────────────
OLD_FEE_EARLY = (
    '        # ── Fix 5: Fee question + selected_tour → redirect to detail_pdf ────\n'
    '        # ป้องกัน booking SM ตัดสาย → fee question ต้อง detail_pdf เสมอ\n'
    '        _FEE_KW_EARLY = {"ค่าทิป", "ทิปไกด์", "ทิปคนขับ", "มัดจำ", "ค่ามัดจำ",\n'
    '                         "วีซ่า", "ค่าวีซ่า", "พักเดี่ยว", "ค่าพักเดี่ยว",\n'
    '                         "จ่ายจริง", "ค่าใช้จ่าย", "ราคาจริง"}\n'
    '        _is_fee_q_early = any(k in text for k in _FEE_KW_EARLY)\n'
    '        if _is_fee_q_early and ctx.get("selected_tour"):'
)
NEW_FEE_EARLY = (
    '        # ── Fix 5: Fee question + selected_tour → redirect to detail_pdf ────\n'
    '        # _FEE_KW_EARLY + _is_fee_q_early defined above (before clear guard)\n'
    '        if _is_fee_q_early and ctx.get("selected_tour"):'
)

if OLD_FEE_EARLY in content:
    content = content.replace(OLD_FEE_EARLY, NEW_FEE_EARLY, 1)
    print("✅ Patch A-2: removed duplicate _FEE_KW_EARLY block")
else:
    print("❌ Patch A-2 FAILED — token mismatch")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# PATCH B: Pre-booking detail template when date+pax known but contact missing
# ─────────────────────────────────────────────────────────────────────────────
OLD_SM_ASK = (
    '                if action in ("search", "flash_sale", "reply") and not _new_fields:\n'
    '                    # ไม่มีข้อมูลใหม่ และ action ไม่ใช่ detail/handoff → ถามเฉพาะ field ที่ขาด\n'
    '                    action = "reply"\n'
    '                    should_search = False\n'
    '                    missing_field_to_ask = None\n'
    '                    # ตอบสั้นทันทีโดยไม่ผ่าน LLM\n'
    '                    if _next_ask:\n'
    '                        _ask_msg = _BOOKING_ASK.get(_next_ask, "รบกวนขอข้อมูลเพิ่มเติมด้วยนะคะ \U0001f64f")\n'
    '                        send_message(sender_id, _ask_msg)\n'
    '                        save_to_history(sender_id, "user", text)\n'
    '                        save_to_history(sender_id, "assistant", _ask_msg)\n'
    '                        log_chat_event(sender_id, "booking_field_ask", text, "booking_sm", lead_stage, ctx)\n'
    '                        return jsonify({"status": "ok"}), 200'
)
NEW_SM_ASK = (
    '                if action in ("search", "flash_sale", "reply") and not _new_fields:\n'
    '                    # ไม่มีข้อมูลใหม่ และ action ไม่ใช่ detail/handoff → ถามเฉพาะ field ที่ขาด\n'
    '                    action = "reply"\n'
    '                    should_search = False\n'
    '                    missing_field_to_ask = None\n'
    '                    # ── P1 Patch B: Pre-booking detail template ────────────\n'
    '                    # เมื่อรู้วันเดินทาง+จำนวนคนแล้ว แต่ยังไม่มีชื่อ/เบอร์\n'
    '                    # ส่ง summary template ก่อน แล้วค่อยถาม contact info\n'
    '                    _has_date = bool(_bf.get("departure_date"))\n'
    '                    _has_pax  = bool(_bf.get("pax"))\n'
    '                    _has_contact = bool(_bf.get("contact_name") or _bf.get("phone") or _bf.get("line_id"))\n'
    '                    _detail_sent = bool(ctx.get("pre_booking_detail_sent"))\n'
    '                    if _has_date and _has_pax and not _has_contact and not _detail_sent:\n'
    '                        _sel_t    = ctx.get("selected_tour") or {}\n'
    '                        _t_name   = ctx.get("selected_tour_name") or _sel_t.get("name", "")\n'
    '                        _t_code   = ctx.get("selected_tour_code") or _sel_t.get("tour_code_real", "") or ""\n'
    '                        _t_wc     = ctx.get("selected_tour_web_code") or _sel_t.get("web_code", "") or ""\n'
    '                        _t_price  = _sel_t.get("price_min") or _sel_t.get("price", "")\n'
    '                        _t_dep    = _bf.get("departure_date", "")\n'
    '                        _t_pax    = _bf.get("pax", "")\n'
    '                        _cust_name= ctx.get("customer_name") or ""\n'
    '                        _sel_wc2  = _t_wc or _sel_t.get("web_code", "")\n'
    '                        _db_fee2  = fetch_fee_from_db(_sel_wc2) if _sel_wc2 else {}\n'
    '                        def _fv2(f): return _db_fee2.get(f) or _sel_t.get(f)\n'
    '                        _tip2     = _fv2("tip_fee")\n'
    '                        _dep2     = _fv2("deposit")\n'
    '                        _single2  = _fv2("single_supplement")\n'
    '                        _visa_s2  = _fv2("visa_status") or _fv2("visa_fee")\n'
    '                        _name_str = f"คุณ {_cust_name} " if _cust_name else ""\n'
    '                        def _fmt_fee(v, unit=""):\n'
    '                            if isinstance(v, (int, float)) and v: return f"{v:,}{unit}"\n'
    '                            return str(v) if v else "กำลังเช็ก ⏳"\n'
    '                        _price_str = _fmt_fee(_t_price, " บาท/ท่าน") if _t_price else "กรุณาเช็กกับทีมงาน"\n'
    '                        _tip_str   = _fmt_fee(_tip2, " บาท/ท่าน")\n'
    '                        _dep_str   = _fmt_fee(_dep2, " บาท")\n'
    '                        _single_str= _fmt_fee(_single2, " บาท")\n'
    '                        _visa_str  = str(_visa_s2) if _visa_s2 else "กำลังเช็ก ⏳"\n'
    '                        _lines = [\n'
    '                            f"ได้เลยค่ะ {_name_str}\U0001f60a",\n'
    '                            "สรุปรายละเอียดโปรแกรมที่เลือกนะคะ",\n'
    '                            "",\n'
    '                            f"✈️ {_t_name}",\n'
    '                            f"\U0001f3f7 รหัสทัวร์: {_t_code}",\n'
    '                            f"\U0001f511 รหัสเว็บ: {_t_wc}",\n'
    '                            f"\U0001f4c5 วันเดินทาง: {_t_dep}",\n'
    '                            f"\U0001f465 จำนวน: {_t_pax} ท่าน",\n'
    '                            f"\U0001f4b0 ราคาเริ่ม: {_price_str}",\n'
    '                            "",\n'
    '                            "รายละเอียดที่ต้องเช็กก่อนยืนยันจอง:",\n'
    '                            f"\U0001f91d ค่าทิปไกด์: {_tip_str}",\n'
    '                            f"\U0001f4b3 มัดจำ: {_dep_str}",\n'
    '                            f"\U0001f6cf️ พักเดี่ยว: {_single_str}",\n'
    '                            f"\U0001f6c2 วีซ่า: {_visa_str}",\n'
    '                            "",\n'
    '                            f"เดี๋ยวส่งให้ทีมงานเช็กยอดจ่ายจริง + ที่นั่งรอบ {_t_dep} ให้นะคะ",\n'
    '                            "ขอชื่อผู้ติดต่อและเบอร์โทรไว้ให้ทีมงานยืนยันได้เลยค่ะ \U0001f60a",\n'
    '                        ]\n'
    '                        _detail_msg = "\\n".join(_lines)\n'
    '                        ctx["pre_booking_detail_sent"] = True\n'
    '                        save_context(sender_id, ctx)\n'
    '                        send_message(sender_id, _detail_msg)\n'
    '                        save_to_history(sender_id, "user", text)\n'
    '                        save_to_history(sender_id, "assistant", _detail_msg)\n'
    '                        log_chat_event(sender_id, "pre_booking_detail", text, "booking_sm", lead_stage, ctx)\n'
    '                        return jsonify({"status": "ok"}), 200\n'
    '                    # ── ตอบสั้นทันทีโดยไม่ผ่าน LLM ────────────────────────\n'
    '                    if _next_ask:\n'
    '                        _ask_msg = _BOOKING_ASK.get(_next_ask, "รบกวนขอข้อมูลเพิ่มเติมด้วยนะคะ \U0001f64f")\n'
    '                        send_message(sender_id, _ask_msg)\n'
    '                        save_to_history(sender_id, "user", text)\n'
    '                        save_to_history(sender_id, "assistant", _ask_msg)\n'
    '                        log_chat_event(sender_id, "booking_field_ask", text, "booking_sm", lead_stage, ctx)\n'
    '                        return jsonify({"status": "ok"}), 200'
)

if OLD_SM_ASK in content:
    content = content.replace(OLD_SM_ASK, NEW_SM_ASK, 1)
    print("✅ Patch B: Pre-booking detail template applied")
else:
    print("❌ Patch B FAILED — token mismatch")
    # Try to find partial match for debug
    snippet = '                    # ตอบสั้นทันทีโดยไม่ผ่าน LLM'
    idx = content.find(snippet)
    print(f"  Debug: 'ตอบสั้น' found at index {idx}")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# PATCH C: Expand _FEE_KEYWORDS
# ─────────────────────────────────────────────────────────────────────────────
OLD_FEE_KW = (
    '        _FEE_KEYWORDS = {"ค่าทิป", "ทิปไกด์", "ทิปคนขับ", "มัดจำ", "ค่ามัดจำ",\n'
    '                         "วีซ่า", "ค่าวีซ่า", "พักเดี่ยว", "ค่าพักเดี่ยว",\n'
    '                         "จ่ายจริง", "ราคาจริง", "จ่ายทั้งหมด"}'
)
NEW_FEE_KW = (
    '        _FEE_KEYWORDS = {"ค่าทิป", "ทิปไกด์", "ทิปคนขับ", "มัดจำ", "ค่ามัดจำ",\n'
    '                         "วีซ่า", "ค่าวีซ่า", "พักเดี่ยว", "ค่าพักเดี่ยว",\n'
    '                         "จ่ายจริง", "ราคาจริง", "จ่ายทั้งหมด",\n'
    '                         "ราคารวมทิป", "รวมทิป", "ยอดจ่ายจริง", "ทิปรวม",\n'
    '                         "ค่าใช้จ่ายทั้งหมด", "ราคารวมทั้งหมด"}'
)

if OLD_FEE_KW in content:
    content = content.replace(OLD_FEE_KW, NEW_FEE_KW, 1)
    print("✅ Patch C: _FEE_KEYWORDS expanded")
else:
    print("❌ Patch C FAILED — token mismatch")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# PATCH D-1: Reset pre_booking_detail_sent on option selection
# ─────────────────────────────────────────────────────────────────────────────
OLD_OPTION_LOCK = (
    '                ctx["booking_context_locked"] = True\n'
    '                if not ctx.get("booking_fields"):\n'
    '                    ctx["booking_fields"] = {}\n'
    '                ctx["pending_action"] = "collect_booking_info"'
)
NEW_OPTION_LOCK = (
    '                ctx["booking_context_locked"] = True\n'
    '                if not ctx.get("booking_fields"):\n'
    '                    ctx["booking_fields"] = {}\n'
    '                ctx["pending_action"] = "collect_booking_info"\n'
    '                ctx["pre_booking_detail_sent"] = False  # reset on new selection'
)

if OLD_OPTION_LOCK in content:
    content = content.replace(OLD_OPTION_LOCK, NEW_OPTION_LOCK, 1)
    print("✅ Patch D-1: reset pre_booking_detail_sent on option selection")
else:
    print("❌ Patch D-1 FAILED — token mismatch")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# PATCH D-2: Reset pre_booking_detail_sent in CODE_LOCK path
# ─────────────────────────────────────────────────────────────────────────────
OLD_CODE_LOCK = (
    '                ctx["booking_context_locked"] = True\n'
    '                if not ctx.get("booking_fields"):\n'
    '                    ctx["booking_fields"] = {}\n'
    '                action = "detail_pdf"'
)
NEW_CODE_LOCK = (
    '                ctx["booking_context_locked"] = True\n'
    '                if not ctx.get("booking_fields"):\n'
    '                    ctx["booking_fields"] = {}\n'
    '                ctx["pre_booking_detail_sent"] = False  # reset on new selection\n'
    '                action = "detail_pdf"'
)

if OLD_CODE_LOCK in content:
    content = content.replace(OLD_CODE_LOCK, NEW_CODE_LOCK, 1)
    print("✅ Patch D-2: reset pre_booking_detail_sent in CODE_LOCK path")
else:
    print("❌ Patch D-2 FAILED — token mismatch")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Write result
# ─────────────────────────────────────────────────────────────────────────────
with open('/tmp/app_p1_patched.py', 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\nPatched: {len(content)} bytes, {content.count(chr(10))+1} lines")
print(f"Delta: {len(content) - original_len:+d} bytes")
