"""P3 Patch — Deictic reference + single-option auto-select fix"""
import sys

with open('/tmp/app_p2b_patched.py', 'r', encoding='utf-8') as f:
    content = f.read()

orig_len = len(content)
print(f"Original: {orig_len} bytes, {content.count(chr(10))+1} lines")

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 1: Extend parse_option_index_rule_based() — add last_opts_count param
# and deictic / short-affirmative detection for single-option context
# ─────────────────────────────────────────────────────────────────────────────
OLD_PARSE_SIG = (
    'def parse_option_index_rule_based(text: str):\n'
    '    """Fast rule-based parser สำหรับ \'ตัวที่ 1\', \'ตัวแรก\', \'1\' ฯลฯ\n'
    '    Returns 1-indexed int or None — ไม่ต้องรอ Claude"""\n'
    '    import re as _re\n'
    '    t = text.strip()\n'
)

NEW_PARSE_SIG = (
    'def parse_option_index_rule_based(text: str, last_opts_count: int = 0):\n'
    '    """Fast rule-based parser สำหรับ \'ตัวที่ 1\', \'ตัวแรก\', \'1\' ฯลฯ\n'
    '    Returns 1-indexed int or None — ไม่ต้องรอ Claude\n'
    '    last_opts_count: ใช้ deictic+single-option detection"""\n'
    '    import re as _re\n'
    '    t = text.strip()\n'
    '    # ── Deictic reference — "ตัวนี้"/"สนใจครับ" + 1 option → index 1 ──────\n'
    '    _DEICTIC_KW = {\n'
    '        "ตัวนี้", "โปรนี้", "อันนี้", "ตัวล่าสุด", "ตัวเมื่อกี้",\n'
    '        "ตัวดังกล่าว", "ตัวที่บอก", "ที่บอกมา", "ที่แนะนำ",\n'
    '        "ขอรายละเอียด", "รายละเอียดครับ", "รายละเอียดค่ะ",\n'
    '        "เอาตัวนี้", "เอาอันนี้", "โอเคตัวนี้",\n'
    '        "ตกลงตัวนี้", "เลือกตัวนี้", "จองตัวนี้",\n'
    '    }\n'
    '    _SHORT_AFFIRM = {\n'
    '        "สนใจครับ", "สนใจค่ะ", "สนใจนะคะ", "สนใจนะครับ",\n'
    '        "โอเค", "โอเคครับ", "โอเคค่ะ",\n'
    '        "ได้เลย", "ได้เลยครับ", "ได้เลยค่ะ",\n'
    '        "ตกลงครับ", "ตกลงค่ะ",\n'
    '        "เอาเลย", "เอาเลยครับ", "เอาเลยค่ะ",\n'
    '    }\n'
    '    if last_opts_count == 1:\n'
    '        if any(k in t for k in _DEICTIC_KW):\n'
    '            return 1\n'
    '        if t in _SHORT_AFFIRM or (len(t) <= 15 and any(k in t for k in _SHORT_AFFIRM)):\n'
    '            return 1\n'
)

if OLD_PARSE_SIG in content:
    content = content.replace(OLD_PARSE_SIG, NEW_PARSE_SIG, 1)
    print("✅ Patch 1: parse_option_index_rule_based() extended with deictic support")
else:
    print("❌ Patch 1 FAILED — anchor not found")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 2: Pass last_opts_count to parse_option_index_rule_based()
# ─────────────────────────────────────────────────────────────────────────────
OLD_CALL = (
    '        _rule_option_idx = parse_option_index_rule_based(text) if _last_opts_count > 0 else None\n'
)
NEW_CALL = (
    '        _rule_option_idx = parse_option_index_rule_based(text, _last_opts_count) if _last_opts_count > 0 else None\n'
)

if OLD_CALL in content:
    content = content.replace(OLD_CALL, NEW_CALL, 1)
    print("✅ Patch 2: passing last_opts_count to parser")
else:
    print("❌ Patch 2 FAILED — anchor not found")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 3: "ขอรายละเอียด" + existing selected_tour → direct detail_pdf
# ─────────────────────────────────────────────────────────────────────────────
OLD_FEE_REDIRECT = (
    '        # ── Fix 5: Fee question + selected_tour → redirect to detail_pdf ────\n'
    '        # _FEE_KW_EARLY + _is_fee_q_early defined above (before clear guard)\n'
    '        if _is_fee_q_early and ctx.get("selected_tour"):\n'
    '            action        = "detail_pdf"\n'
    '            should_search = False\n'
    '            missing_field_to_ask = None\n'
    '            logger.info(f"[FEE_REDIRECT] fee question + selected_tour → detail_pdf")\n'
)
NEW_FEE_REDIRECT = (
    '        # ── Fix 5: Fee question + selected_tour → redirect to detail_pdf ────\n'
    '        # _FEE_KW_EARLY + _is_fee_q_early defined above (before clear guard)\n'
    '        if _is_fee_q_early and ctx.get("selected_tour"):\n'
    '            action        = "detail_pdf"\n'
    '            should_search = False\n'
    '            missing_field_to_ask = None\n'
    '            logger.info(f"[FEE_REDIRECT] fee question + selected_tour → detail_pdf")\n'
    '\n'
    '        # ── Fix 5b: "ขอรายละเอียด"/"สนใจตัวนี้" + selected_tour → detail ───\n'
    '        _DETAIL_KW = {"ขอรายละเอียด", "รายละเอียดครับ", "รายละเอียดค่ะ",\n'
    '                      "รายละเอียดเพิ่ม", "ดูรายละเอียด", "อยากรู้รายละเอียด",\n'
    '                      "สนใจตัวนี้", "เอาตัวนี้", "สนใจโปรนี้", "ดูโปรนี้"}\n'
    '        _is_detail_req = any(k in text for k in _DETAIL_KW)\n'
    '        if _is_detail_req and ctx.get("selected_tour") and action not in ("detail_pdf", "handoff"):\n'
    '            action        = "detail_pdf"\n'
    '            should_search = False\n'
    '            missing_field_to_ask = None\n'
    '            logger.info(f"[DETAIL_REDIRECT] detail request + selected_tour → detail_pdf")\n'
)

if OLD_FEE_REDIRECT in content:
    content = content.replace(OLD_FEE_REDIRECT, NEW_FEE_REDIRECT, 1)
    print("✅ Patch 3: ขอรายละเอียด + selected_tour → detail_pdf redirect")
else:
    print("❌ Patch 3 FAILED — anchor not found")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Write result
# ─────────────────────────────────────────────────────────────────────────────
with open('/tmp/app_p3_patched.py', 'w', encoding='utf-8') as f:
    f.write(content)
print(f"\nPatched: {len(content)} bytes, {content.count(chr(10))+1} lines")
print(f"Delta: {len(content) - orig_len:+d} bytes")
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    