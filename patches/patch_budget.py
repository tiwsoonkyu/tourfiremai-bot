"""Budget-Aware Tour Recommendation Patch
Patches:
  1. Add budget_type to _EMPTY_CTX
  2. Add detect_budget_type() + select_budget_tiers() functions
  3. process_message(): detect budget_type, widen fetch pool, apply tier ranking
  4. _system_prompt(): show budget_type label
  5. generate_response(): smarter budget hint based on type + amount
"""
import sys

with open('/tmp/app_budget.py', 'r', encoding='utf-8') as f:
    content = f.read()

orig_len = len(content)
print(f"Original: {orig_len} bytes, {content.count(chr(10))+1} lines")

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 1: Add budget_type to _EMPTY_CTX
# ─────────────────────────────────────────────────────────────────────────────
OLD_EMPTY_CTX = '    "budget_per_person": None,\n'
NEW_EMPTY_CTX = (
    '    "budget_per_person": None,\n'
    '    "budget_type": None,           # \'strict\'|\'flexible\'|\'unknown\'\n'
)

cnt = content.count(OLD_EMPTY_CTX)
if cnt == 1:
    content = content.replace(OLD_EMPTY_CTX, NEW_EMPTY_CTX, 1)
    print("✅ Patch 1: budget_type added to _EMPTY_CTX")
elif cnt == 0:
    print("❌ Patch 1 FAILED — anchor not found")
    sys.exit(1)
else:
    # multiple hits — replace inside the _EMPTY_CTX block specifically
    # Find the block and replace first occurrence
    idx = content.find('"_EMPTY_CTX"') if '"_EMPTY_CTX"' in content else content.find('_EMPTY_CTX = {')
    if idx == -1:
        print("❌ Patch 1 FAILED — cannot locate _EMPTY_CTX block")
        sys.exit(1)
    first_occur = content.find(OLD_EMPTY_CTX, idx)
    content = content[:first_occur] + NEW_EMPTY_CTX + content[first_occur + len(OLD_EMPTY_CTX):]
    print(f"✅ Patch 1: budget_type added to _EMPTY_CTX (replaced first of {cnt})")

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 2: Add detect_budget_type() + select_budget_tiers() functions
# Placed before def extract_booking_fields_from_text
# ─────────────────────────────────────────────────────────────────────────────
OLD_EXTRACT_SIG = (
    '    return {}\n'
    '\n'
    'def extract_booking_fields_from_text(text: str, existing: dict) -> dict:\n'
)

NEW_EXTRACT_SIG = (
    '    return {}\n'
    '\n'
    '\n'
    'def detect_budget_type(text: str) -> str:\n'
    '    """ตรวจจับว่าลูกค้ากำหนดงบแน่น (strict) หรือยืดหยุ่น (flexible)\n'
    '    คืน \'strict\'|\'flexible\'|\'unknown\'\n'
    '    ใช้ก่อน fetch_tours เพื่อตัดสินใจว่าจะขยาย pool หรือไม่\n'
    '    """\n'
    '    _STRICT_KW = {\n'
    '        "ไม่เกิน", "จำกัด", "งบแค่นี้", "แค่นี้พอ", "ไม่มีเพิ่ม",\n'
    '        "ไม่อยากเกิน", "ขอไม่เกิน", "ขอแค่", "ได้แค่",\n'
    '        "งบเท่านี้", "เกินไม่ได้",\n'
    '    }\n'
    '    _FLEX_KW = {\n'
    '        "ประมาณ", "สบายๆ", "ไม่ติด", "เปิดกว้าง", "ยืดหยุ่น",\n'
    '        "ไม่ยึด", "ไม่ค่อยยึด", "ประมาณนี้ก็ได้",\n'
    '        "ได้เพิ่มนิดหน่อย", "เพิ่มได้นิดหน่อย",\n'
    '        "คุ้มค่าก็โอเค", "คุ้มก็โอเค",\n'
    '    }\n'
    '    if any(k in text for k in _STRICT_KW):\n'
    '        return "strict"\n'
    '    if any(k in text for k in _FLEX_KW):\n'
    '        return "flexible"\n'
    '    return "unknown"\n'
    '\n'
    '\n'
    'def select_budget_tiers(tours: list, budget: int, budget_type: str) -> list:\n'
    '    """จัดเรียงทัวร์ตาม budget tier — value / recommended / upgrade\n'
    '    คืน list เรียงลำดับตามความเหมาะสมกับงบ (ไม่ตัดทิ้ง)\n'
    '\n'
    '    Tiers (เทียบงบ):\n'
    '      value       : 40–65% ของงบ — ถูก ดูคุ้ม\n'
    '      recommended : 65–85% ของงบ — ราคาเหมาะ คุ้มสุด\n'
    '      upgrade     : 85–105% (flexible) หรือ 85–100% (strict)\n'
    '      outlier     : ที่เหลือ (เรียงต่อท้าย)\n'
    '\n'
    '    Premium route bonus: ฮอกไกโด|ยุโรป|สแกน|นอร์เวย์|สวีเดน|ฟินแลนด์|ไอซ์แลนด์|อเมริกา\n'
    '    Full-service airline bonus: TG|JL|NH|SQ|CX|MH|QR|EK|EY|BA|LH\n'
    '    """\n'
    '    if not tours or not budget:\n'
    '        return tours\n'
    '\n'
    '    _PREMIUM_ROUTES = {\n'
    '        "ฮอกไกโด", "ยุโรป", "สแกนดิเนเวีย", "นอร์เวย์", "สวีเดน",\n'
    '        "ฟินแลนด์", "ไอซ์แลนด์", "อเมริกา", "สวิส", "ออสเตรีย",\n'
    '    }\n'
    '    _FSC_AIRLINES = {"TG", "JL", "NH", "SQ", "CX", "MH", "QR", "EK", "EY", "BA", "LH"}\n'
    '\n'
    '    # Determine ceiling for upgrade tier\n'
    '    upgrade_ceil = budget * 1.05 if budget_type != "strict" else budget\n'
    '\n'
    '    def _score(t):\n'
    '        """คืน (tier_rank, bonus) — tier_rank ต่ำ = แสดงก่อน"""\n'
    '        pm = t.get("price_min") or t.get("price")\n'
    '        try:\n'
    '            price = int(str(pm).replace(",", "").replace(" ", ""))\n'
    '        except (TypeError, ValueError):\n'
    '            return (99, 0)  # ไม่รู้ราคา → ท้ายสุด\n'
    '\n'
    '        ratio = price / budget\n'
    '        if ratio < 0.40:\n'
    '            tier = 3  # ถูกเกินไป — แสดงหลัง recommended\n'
    '        elif ratio <= 0.65:\n'
    '            tier = 1  # value\n'
    '        elif ratio <= 0.85:\n'
    '            tier = 0  # recommended — แสดงก่อนเพื่อน\n'
    '        elif price <= upgrade_ceil:\n'
    '            tier = 2  # upgrade\n'
    '        else:\n'
    '            tier = 4  # outlier — เกินงบ\n'
    '\n'
    '        bonus = 0\n'
    '        name = (t.get("name") or "").upper()\n'
    '        if any(k.upper() in name for k in _PREMIUM_ROUTES):\n'
    '            bonus -= 1  # premium route → แสดงขึ้นมาอีกนิด\n'
    '        airline = (t.get("airline") or "").upper().strip()\n'
    '        if any(a in airline for a in _FSC_AIRLINES):\n'
    '            bonus -= 1  # full-service carrier → priority\n'
    '\n'
    '        return (tier, bonus, price)\n'
    '\n'
    '    return sorted(tours, key=_score)\n'
    '\n'
    '\n'
    'def extract_booking_fields_from_text(text: str, existing: dict) -> dict:\n'
)

if OLD_EXTRACT_SIG in content:
    content = content.replace(OLD_EXTRACT_SIG, NEW_EXTRACT_SIG, 1)
    print("✅ Patch 2: detect_budget_type() + select_budget_tiers() added")
else:
    print("❌ Patch 2 FAILED — anchor not found")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 3: process_message() — detect budget_type, widen fetch, apply tiers
# ─────────────────────────────────────────────────────────────────────────────
OLD_FETCH_BLOCK = (
    '                budget_max = None\n'
    '                if ctx and ctx.get("budget_per_person"):\n'
    '                    try:\n'
    '                        b = str(ctx["budget_per_person"]).replace(",","").replace(" ","")\n'
    '                        budget_max = int(b)\n'
    '                    except Exception:\n'
    '                        pass\n'
    '                result = fetch_tours(country_id, city_hint=city_hint, budget_max=budget_max)\n'
)

NEW_FETCH_BLOCK = (
    '                budget_max = None\n'
    '                if ctx and ctx.get("budget_per_person"):\n'
    '                    try:\n'
    '                        b = str(ctx["budget_per_person"]).replace(",","").replace(" ","")\n'
    '                        budget_max = int(b)\n'
    '                    except Exception:\n'
    '                        pass\n'
    '                # ── Detect budget_type from current message ──────────────\n'
    '                if budget_max and not ctx.get("budget_type"):\n'
    '                    ctx["budget_type"] = detect_budget_type(text)\n'
    '                    logger.info(f"[BUDGET_TYPE] detected={ctx[\'budget_type\']} budget={budget_max}")\n'
    '                # ── Widen fetch pool for flexible/unknown budgets ─────────\n'
    '                _fetch_budget = budget_max\n'
    '                if _fetch_budget and ctx.get("budget_type") != "strict":\n'
    '                    _fetch_budget = int(_fetch_budget * 1.20)  # fetch up to 120%\n'
    '                result = fetch_tours(country_id, city_hint=city_hint, budget_max=_fetch_budget)\n'
)

if OLD_FETCH_BLOCK in content:
    content = content.replace(OLD_FETCH_BLOCK, NEW_FETCH_BLOCK, 1)
    print("✅ Patch 3a: budget_type detection + widen fetch pool")
else:
    print("❌ Patch 3a FAILED — anchor not found")
    sys.exit(1)

# ── After fetch_tours(), apply tier ranking when budget known ─────────────────
OLD_SOONEST = (
    '                # ── Sort by soonest departure when user wants เร็วๆนี้ ────────\n'
    '                if departure_urgency == "soonest" and tour_meta:\n'
    '                    tour_meta = sorted(tour_meta, key=_earliest_departure_sort_key)\n'
    '                    logger.info(f"[SOONEST] sorted {len(tour_meta)} tours by nearest departure")\n'
)

NEW_SOONEST = (
    '                # ── Budget-aware tier ranking ─────────────────────────────\n'
    '                if tour_meta and budget_max:\n'
    '                    _btype = ctx.get("budget_type") or "unknown"\n'
    '                    tour_meta = select_budget_tiers(tour_meta, budget_max, _btype)\n'
    '                    logger.info(f"[BUDGET_TIERS] ranked {len(tour_meta)} tours budget={budget_max} type={_btype}")\n'
    '                # ── Sort by soonest departure when user wants เร็วๆนี้ ────────\n'
    '                if departure_urgency == "soonest" and tour_meta:\n'
    '                    tour_meta = sorted(tour_meta, key=_earliest_departure_sort_key)\n'
    '                    logger.info(f"[SOONEST] sorted {len(tour_meta)} tours by nearest departure")\n'
)

if OLD_SOONEST in content:
    content = content.replace(OLD_SOONEST, NEW_SOONEST, 1)
    print("✅ Patch 3b: select_budget_tiers() injected after fetch_tours()")
else:
    print("❌ Patch 3b FAILED — anchor not found")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 4: _system_prompt() — show budget_type label
# ─────────────────────────────────────────────────────────────────────────────
OLD_SYS_BUDGET = (
    '        if ctx.get("budget_per_person"):\n'
    '            parts.append(f"งบ/คน: {ctx[\'budget_per_person\']:,} บาท" if isinstance(ctx[\'budget_per_person\'], (int, float)) else f"งบ/คน: {ctx[\'budget_per_person\']}")\n'
)

NEW_SYS_BUDGET = (
    '        if ctx.get("budget_per_person"):\n'
    '            _btype_label = {"strict": " (งบแน่น ห้ามเสนอเกิน)", "flexible": " (ยืดหยุ่นได้ เสนอตัวคุ้มค่าได้)", "unknown": ""}.get(ctx.get("budget_type") or "unknown", "")\n'
    '            parts.append(f"งบ/คน: {ctx[\'budget_per_person\']:,} บาท{_btype_label}" if isinstance(ctx[\'budget_per_person\'], (int, float)) else f"งบ/คน: {ctx[\'budget_per_person\']}{_btype_label}")\n'
)

if OLD_SYS_BUDGET in content:
    content = content.replace(OLD_SYS_BUDGET, NEW_SYS_BUDGET, 1)
    print("✅ Patch 4: _system_prompt() shows budget_type label")
else:
    print("❌ Patch 4 FAILED — anchor not found")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 5: generate_response() — smarter budget hint
# Replace BOTH budget hint lines (faimai + normal search)
# ─────────────────────────────────────────────────────────────────────────────
OLD_BUDGET_HINT_FAIMAI = (
    '                + (f"\\n[งบลูกค้า {ctx[\'budget_per_person\']} บาท: คัดเฉพาะที่ราคาไม่เกินงบก่อน ถ้าไม่มีให้บอกตรงๆ]" if ctx.get("budget_per_person") else "")\n'
)
NEW_BUDGET_HINT_FAIMAI = (
    '                + (_build_budget_hint(ctx) if ctx and ctx.get("budget_per_person") else "")\n'
)

OLD_BUDGET_HINT_NORMAL = (
    '                + (f"\\n[งบลูกค้า {ctx[\'budget_per_person\']} บาท: คัดเฉพาะที่ราคาไม่เกินงบก่อน ถ้าไม่มีให้โชว์ถูกสุดและบอกว่าเกินเท่าไหร่]" if ctx and ctx.get("budget_per_person") else "")\n'
)
NEW_BUDGET_HINT_NORMAL = (
    '                + (_build_budget_hint(ctx) if ctx and ctx.get("budget_per_person") else "")\n'
)

p5a = content.count(OLD_BUDGET_HINT_FAIMAI)
p5b = content.count(OLD_BUDGET_HINT_NORMAL)

if p5a == 1:
    content = content.replace(OLD_BUDGET_HINT_FAIMAI, NEW_BUDGET_HINT_FAIMAI, 1)
    print("✅ Patch 5a: faimai budget hint replaced")
else:
    print(f"❌ Patch 5a FAILED (count={p5a})")
    sys.exit(1)

if p5b == 1:
    content = content.replace(OLD_BUDGET_HINT_NORMAL, NEW_BUDGET_HINT_NORMAL, 1)
    print("✅ Patch 5b: normal search budget hint replaced")
else:
    print(f"❌ Patch 5b FAILED (count={p5b})")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 6: Add _build_budget_hint() helper before def generate_response()
# ─────────────────────────────────────────────────────────────────────────────
OLD_GEN_SIG = '# ─── AI — Call 2: Generate Response ──────────────────────────────────────────\ndef generate_response(user_message: str, history: list, tour_data: str = "",\n'

NEW_GEN_PREFIX = (
    '# ─── AI — Call 2: Generate Response ──────────────────────────────────────────\n'
    'def _build_budget_hint(ctx: dict) -> str:\n'
    '    """สร้าง budget instruction สำหรับ Claude ตาม budget_type + amount\n'
    '    คืน string ที่ append เข้า user_content ใน generate_response()\n'
    '    """\n'
    '    budget = ctx.get("budget_per_person")\n'
    '    btype  = ctx.get("budget_type") or "unknown"\n'
    '    country = ctx.get("country") or ctx.get("country_name") or ""\n'
    '    if not budget:\n'
    '        return ""\n'
    '    try:\n'
    '        bval = int(str(budget).replace(",", "").replace(" ", ""))\n'
    '    except (TypeError, ValueError):\n'
    '        bval = 0\n'
    '\n'
    '    if btype == "strict":\n'
    '        return (\n'
    '            f"\\n[งบลูกค้า {budget} บาท/คน (งบแน่น): เสนอเฉพาะที่ไม่เกินงบ "\n'
    '            "ถ้าไม่มีให้บอกตรงๆ และแนะนำใกล้เคียงที่สุด]"\n'
    '        )\n'
    '\n'
    '    if btype == "flexible" and bval >= 25000:\n'
    '        country_str = f"ไป{country}" if country else ""\n'
    '        return (\n'
    '            f"\\n[งบลูกค้า {budget} บาท/คน (ยืดหยุ่นได้): "\n'
    '            f"เปิดด้วย \'งบ {budget}/คน {country_str}ได้สบายเลยค่ะ "\n'
    '            "ขอคัดแบบคุ้มกับงบ ไม่ได้เลือกถูกที่สุดอย่างเดียวนะคะ\' "\n'
    '            "แล้วเสนอ: (1) Value ราคาดี คุ้มสุด (2) Recommended ราคาเหมาะ (3) Upgrade ไม่เกินงบมาก "\n'
    '            "เน้นประสบการณ์และสายการบิน ไม่ใช่แค่ราคาถูก]"\n'
    '        )\n'
    '\n'
    '    if bval >= 25000:\n'
    '        country_str = f"ไป{country}" if country else ""\n'
    '        return (\n'
    '            f"\\n[งบลูกค้า {budget} บาท/คน: "\n'
    '            f"เปิดด้วย \'งบ {budget}/คน {country_str}ได้สบายเลยค่ะ "\n'
    '            "ขอคัดแบบคุ้มกับงบ ไม่ได้เลือกถูกที่สุดอย่างเดียวนะคะ\' "\n'
    '            "เสนอ 3 ระดับ: ราคาดี + คุ้มค่าที่สุด + อัพเกรดหน่อย "\n'
    '            "เน้นประสบการณ์และสายการบิน]"\n'
    '        )\n'
    '\n'
    '    # default (low budget or unknown)\n'
    '    return (\n'
    '        f"\\n[งบลูกค้า {budget} บาท/คน: คัดเฉพาะที่ราคาไม่เกินงบก่อน "\n'
    '        "ถ้าไม่มีให้โชว์ถูกสุดและบอกว่าเกินเท่าไหร่]"\n'
    '    )\n'
    '\n'
    '\n'
    'def generate_response(user_message: str, history: list, tour_data: str = "",\n'
)

if content.count(OLD_GEN_SIG) == 1:
    content = content.replace(OLD_GEN_SIG, NEW_GEN_PREFIX, 1)
    print("✅ Patch 6: _build_budget_hint() helper added before generate_response()")
else:
    print(f"❌ Patch 6 FAILED — found {content.count(OLD_GEN_SIG)} matches for 'def generate_response('")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Write result
# ─────────────────────────────────────────────────────────────────────────────
with open('/tmp/app_budget_patched.py', 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\nPatched: {len(content)} bytes, {content.count(chr(10))+1} lines")
print(f"Delta: {len(content) - orig_len:+d} bytes")
