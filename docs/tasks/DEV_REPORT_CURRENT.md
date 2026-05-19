# Dev Report — `DEV-2026-05-19-005` LINE Admin Command Handler Core

**Status:** `READY_FOR_QA`
**Verdict recommendation to QA:** `GO`
**Author:** Codex acting as Dev while Tiw is away
**Date:** 2026-05-19
**Branch:** `v2/s4-followup-vision-ondemand`
**Parent commit:** `a072820`
**Spend this session:** `$0.00` — no live LINE, no live OpenAI, no paid provider, no deploy.

---

## 1. Status

`READY_FOR_QA`. This task wires the QA-cleared `admin_ops` foundation into a deterministic backend command layer that a future LINE webhook adapter can call.

The new layer is pure and testable:

- no live LINE send
- no customer-facing Messenger reply
- no network calls
- no env reads
- no V1 / Make.com / production webhook changes

---

## 2. Files Changed

```text
v2/lib/admin_command_handler.py        NEW
v2/tests/test_admin_command_handler.py NEW
docs/tasks/DEV_REPORT_CURRENT.md       rewritten with this report
docs/tasks/AGENT_STATUS.json           flipped to READY_FOR_QA
```

No changes to fee/PDF/LLM behavior. No migrations.

---

## 3. What Shipped

New module: `v2/lib/admin_command_handler.py`

Public API:

- `AdminCommand`
- `AdminCommandResult`
- `parse_admin_command(text)`
- `handle_admin_command(command_or_text, supabase, *, admin_user_id, memory=None, now=None)`

Supported commands:

- `cases`
- `cases paused`
- `handoffs`
- `case <psid_or_conversation_id>`
- `pause <psid_or_conversation_id> [reason...]`
- `resume <psid_or_conversation_id> [reason...]`
- `help`

Behavior:

- `cases` lists newest open admin cases.
- `cases paused` lists human-paused cases.
- `handoffs` lists open handoffs.
- `case <id>` renders customer/case detail from `admin_ops.get_admin_case`.
- `pause <id>` resolves the case, then calls `admin_ops.pause_bot_for_customer`.
- `resume <id>` resolves the case, then calls `admin_ops.resume_bot_for_customer`.
- Missing target returns a safe admin error and does not create a fake pause.
- Unknown commands return a compact help message.

---

## 4. Safety Controls

Admin output is designed for a staff LINE group:

- Customer PSIDs are masked in visible text.
- Secret-like text is redacted and secret prefixes are not retained in admin output.
- Wholesale/provider names are redacted through the existing response-writer blacklist.
- The command handler does not send messages itself. It returns `AdminCommandResult.admin_text`.
- No production webhook, V1 app, Make.com, Railway, Cloudflare, or Meta integration was touched.

---

## 5. Tests

New tests: `v2/tests/test_admin_command_handler.py`

Coverage:

- Parser recognizes `cases`, `cases paused`, `handoffs`, `case`, `pause`, `resume`, and `help`.
- Parser handles whitespace and unknown commands safely.
- `cases` renders masked/safe admin lines.
- `handoffs` renders open handoffs without raw PSID.
- `case <id>` renders customer context, selected tour, and masked ID.
- `pause <id>` mutates through `pause_bot_for_customer`.
- `resume <id>` mutates through `resume_bot_for_customer`.
- Missing target does not mutate pause state.
- Secret-like values are redacted.
- Configured provider/wholesale-like tokens are redacted.

Commands run:

```text
python -m pytest v2/tests/test_admin_command_handler.py -q
Result: 11 passed

python -m pytest v2/tests/test_admin_ops.py v2/tests/test_admin_command_handler.py -q
Result: 44 passed

python -m pytest v2/tests --ignore=v2/tests/integration --ignore=v2/tests/live --ignore=v2/tests/test_live_openai_health.py -q
Result: 538 passed, 11 skipped, 0 failed
```

Skips were expected: staging DB env not set for integration tests and Flask not installed for webhook tests.

Post-review safety patch:

```text
python -m py_compile v2/lib/admin_command_handler.py v2/tests/test_admin_command_handler.py
Result: passed
```

Note: after the final display-name redaction tweak, the resumed Codex runtime no longer had `pytest` installed, so the last re-check was a syntax/compile pass. The code delta after the full pytest run was limited to routing pause/resume display names through `_safe_text()`.

---

## 6. Scope Guard

Verified:

- V1 untouched.
- Make.com untouched.
- No deploy.
- No production Messenger webhook change.
- No live LINE API call.
- No live OpenAI call.
- No paid-provider call.
- No migration change.
- No fee threshold change.
- No PDF extraction change.
- Secret-shape grep against the two new runtime/test files is clean.

---

## 7. Remaining Risks / Notes

1. This is only the deterministic command core. A future task must wire it into a real LINE admin webhook/adapter.
2. The command syntax currently supports English command words. Thai aliases can be added later after staff agree on exact phrases.
3. `cases` and `handoffs` are in-memory/supabase-helper driven and limited to five visible rows by default in this handler. Dashboard pagination remains a separate task.

---

## 8. Recommended Next Step

QA should review `DEV-2026-05-19-005`.

If QA verdict is `GO`, next Controller choice:

1. Wire this command core into a staging-only LINE admin webhook adapter, or
2. Build a minimal dashboard read API using the same `admin_ops` view-models.

Recommended priority: staging-only LINE admin adapter, because it directly prevents the bot from interrupting active human conversations.
