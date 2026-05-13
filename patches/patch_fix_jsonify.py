"""
Fix 1: Replace return jsonify({"status":"ok"}),200 → return inside process_message()
        (background thread has no Flask app context → jsonify raises RuntimeError)
Fix 2: Guard resolve_selected_tour_from_text with `not _direct_country_fill`
        to stop country name from matching as tour name keyword
"""
import re

with open('/tmp/app_current.py', 'r', encoding='utf-8') as f:
    src = f.read()

ORIGINAL = src  # keep for diff stats

# ── Fix 1: Replace all "return jsonify({"status": "ok"}), 200" inside process_message ──
# These 7 occurrences are at lines 3395, 3447, 3701, 3709, 3913, 3941, 4141
# (lines ≥3106, inside process_message, NOT in real Flask handlers ≥4510)

# We replace the specific pattern with indented `return`
# Each occurrence has different indentation — preserve it
def _replace_in_process_message(source):
    pm_start = source.find('\ndef process_message(')
    pm_end   = source.find('\n@app.route', pm_start)   # first decorator after function
    
    before = source[:pm_start]
    pm_body = source[pm_start:pm_end]
    after   = source[pm_end:]
    
    # Replace "return jsonify({"status": "ok"}), 200" with just "return"
    # Pattern handles any indentation
    pm_body_fixed = re.sub(
        r'([ \t]+)return jsonify\(\{"status": "ok"\}\), 200\n',
        r'\1return\n',
        pm_body
    )
    
    count = pm_body.count('return jsonify({"status": "ok"}), 200')
    print(f"Fix 1: replaced {count} occurrences of return jsonify inside process_message")
    return before + pm_body_fixed + after

src = _replace_in_process_message(src)

# ── Fix 2: Guard resolve_selected_tour_from_text with not _direct_country_fill ──
# Current code:
OLD_GUARD = (
    '        if not ctx.get("selected_tour") and ctx.get("last_options"):\n'
    '            _lopt2 = ctx.get("last_options")\n'
)
NEW_GUARD = (
    '        if not ctx.get("selected_tour") and ctx.get("last_options") and not _direct_country_fill:\n'
    '            _lopt2 = ctx.get("last_options")\n'
)
if OLD_GUARD in src:
    src = src.replace(OLD_GUARD, NEW_GUARD, 1)
    print("Fix 2: added 'not _direct_country_fill' guard to resolve_selected_tour_from_text")
else:
    print("Fix 2: WARNING — old guard pattern NOT found, check code manually")

# ── Verify ──
if 'return jsonify({"status": "ok"}), 200' in src[src.find('\ndef process_message('):src.find('\n@app.route', src.find('\ndef process_message('))]:
    print("ERROR: still has return jsonify inside process_message!")
else:
    print("Verify Fix 1: ✅ no return jsonify inside process_message")

if 'and not _direct_country_fill' in src:
    print("Verify Fix 2: ✅ guard present")

with open('/tmp/app_fixed.py', 'w', encoding='utf-8') as f:
    f.write(src)

print(f"\nOutput: /tmp/app_fixed.py ({len(src)} bytes, {len(src.splitlines())} lines)")
print(f"Delta: {len(src) - len(ORIGINAL):+d} bytes")
