"""P2b — Fix name_keyword scoring: best-match instead of first-match"""
import sys

with open('/tmp/app_p2_patched.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the Priority-4 block inside resolve_selected_tour_from_text
OLD_P4 = (
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

NEW_P4 = (
    '    # Priority 4: BEST name / city keyword match — score all, pick highest\n'
    '    # Scoring avoids false-positive when common words (ไทเป) appear in multiple tours:\n'
    '    # tour with most keyword hits wins (e.g. "ไทเป กวนอิม" scores 2 on ap242807 vs 1 on others)\n'
    '    _TH_STOPWORDS = {"ทัวร์", "โปร", "ราคา", "บาท", "วัน", "คืน", "เที่ยว",\n'
    '                     "ครั้ง", "ท่าน", "คน", "พัก", "บิน", "สาย", "การ"}\n'
    '    _best_tour  = None\n'
    '    _best_score = 0\n'
    '    _best_kw    = ""\n'
    '    for t in last_options:\n'
    '        name = t.get("name") or ""\n'
    '        th_words = [w for w in name.split() if len(w) >= 3 and w not in _TH_STOPWORDS]\n'
    '        _score = sum(1 for w in th_words if w.upper() in text_up or w in text)\n'
    '        if _score > _best_score:\n'
    '            _best_score = _score\n'
    '            _best_tour  = t\n'
    '            _best_kw    = " ".join(w for w in th_words if w.upper() in text_up or w in text)\n'
    '    if _best_tour and _best_score > 0:\n'
    '        return {"tour": _best_tour, "method": "name_keyword", "keyword": _best_kw}\n'
    '\n'
    '    return {}\n'
)

if OLD_P4 in content:
    content = content.replace(OLD_P4, NEW_P4, 1)
    print("✅ P2b: name_keyword scoring fixed to best-match")
else:
    print("❌ P2b FAILED — anchor not found")
    sys.exit(1)

with open('/tmp/app_p2b_patched.py', 'w', encoding='utf-8') as f:
    f.write(content)
print(f"Written: {len(content)} bytes, {content.count(chr(10))+1} lines")
