# QA Report — `QA-2026-05-19-005`

**Verdict:** `GO_WITH_NOTES`
**Author:** Claude Cowork QA (read-only review)
**Date:** 2026-05-19
**Controller:** Codex
**Paired Dev task:** `DEV-2026-05-19-005` (LINE Admin Command Handler Core)
**Branch named in control files:** `v2/s4-followup-vision-ondemand`
**Parent commit (per Dev report):** `a072820`

---

## 1. Verdict

**`GO_WITH_NOTES`.** The new `v2/lib/admin_command_handler.py` is a clean, deterministic command core wrapping the QA-cleared `admin_ops` foundation. The parser covers every command in the QA scope, every mutating command routes through `admin_ops` (not direct SQL or fake-mutates), and admin output is filtered through both the PSID redactor and the wholesale blacklist before being returned. The accompanying test file exercises parsing, listing, case-detail, pause, resume, missing-target, and two leakage-safety paths. All eleven test functions have plausible, on-target assertions.

I am voting `GO_WITH_NOTES` instead of `GO` because of three small post-merge follow-ups (all `🟡 Medium` or below) — none blocks the cleanup-task-style green light, but each is a follow-up Codex should track before the next ticket wires this core into a real LINE webhook adapter:

1. The display-name `_safe_text` routing on the **pause** and **resume** admin_text paths was added post-pytest-run; no test asserts that a wholesale-named or secret-shaped display name is redacted in those two paths specifically (the existing redaction tests cover `cases` and `case <id>` only).
2. `admin_user_id` is persisted to `bot_pauses.resumed_by` (and into the fallback `reason` string) raw — fine for admin-only fields, but worth tightening if `admin_user_id` could ever carry secret-like content.
3. **There is no authorization check inside `handle_admin_command`.** This is correct for the *core* (Dev report acknowledges a future adapter will do auth), but the adapter ticket must enforce a staff allow-list before forwarding text to `handle_admin_command(...)`. Adding a one-liner "authorization happens at the adapter, not here" docstring to the module would lock that contract in.

The product invariant — *"when a human/admin is handling a customer, the bot must not interrupt"* — is preserved: the handler itself sends nothing; pause routes through `admin_ops.pause_bot_for_customer` which sets `conversations.is_human_paused=True` and `state='human_paused'`, which the existing orchestrator pause-guard already short-circuits on.

---

## 2. Scope Reviewed

| # | Required-reading file | Read? | Notes |
|---|------------------------|-------|-------|
| 1 | `docs/AI_COMMAND_CENTER.md` | ✅ | Hard-rules + handoff rule confirmed |
| 2 | `docs/tasks/CURRENT_QA_TASK.md` | ✅ | Task ID `QA-2026-05-19-005`, paired with `DEV-2026-05-19-005`; 15-item check list |
| 3 | `docs/tasks/TASK_LOG.md` | ✅ | `DEV-2026-05-19-005` `READY_FOR_QA`, `QA-2026-05-19-005` `PENDING` — control files aligned this session (drift from earlier today is fixed) |
| 4 | `docs/tasks/DEV_REPORT_CURRENT.md` | ✅ | Dev recommends `GO`; spend $0.00; 538 passed / 11 skipped / 0 failed |
| 5 | `docs/tasks/AGENT_STATUS.json` | ✅ | `current_dev_task=DEV-2026-05-19-005`, `current_qa_task=QA-2026-05-19-005`, status `READY_FOR_QA` |
| 6 | `v2/lib/admin_ops.py` | ✅ | 833 lines; QA-cleared in `DEV-2026-05-19-004`; reused unchanged here |
| 7 | `v2/lib/admin_command_handler.py` | ✅ | 357 lines; new module |
| 8 | `v2/tests/test_admin_ops.py` | ✅ (head + counts) | 603 lines; not in this task's diff |
| 9 | `v2/tests/test_admin_command_handler.py` | ✅ | 220 lines; new test module — 11 test functions |

**Files NOT readable in this workspace:** `v2/lib/redactor.py`, `v2/lib/response_writer.py`, `v2/lib/state_machine.py`, `v2/lib/memory.py`, `v2/tests/conftest.py`, `.git/**`. The mount only exposes the four files Codex listed plus `admin_ops.py`. Consequently, pytest cannot be executed from here (missing siblings and fixtures); Dev's test counts (11 / 44 / 538) are taken at face value but are consistent with the prior known baseline of 501 (501 + ~37 net new from `DEV-2026-05-19-004` + `DEV-2026-05-19-005` ≈ 538).

---

## 3. Evidence Against the 15-Item QA Check List

| # | QA check | Verdict | Evidence |
|---|----------|:------:|----------|
| 1 | Dev stayed on V2 scope only | ✅ PASS | `admin_command_handler.py` imports only `v2.lib.redactor`, `v2.lib.admin_ops`, `v2.lib.response_writer` (`_WHOLESALE_BLACKLIST` only). Test imports `v2.lib.admin_command_handler`, `v2.lib.admin_ops`. No V1 paths. |
| 2 | V1 production code not changed | ✅ PASS (circumstantial) | Two new files under `v2/lib/` and `v2/tests/`. No `app.py` / `scraper.py` / `tourfiremai-bot-dev/` / `cloudflare-worker.js` / `Procfile` / `railway.json` touched. Cannot run `git diff` from this workspace, but the Dev report's file-list (2 code + 2 docs) is self-consistent. |
| 3 | Make.com / Cloudflare / Meta webhook untouched | ✅ PASS | Zero references to `make.com`, `integromat`, `cloudflare`, `railway`, `webhook_proxy`, `messenger`, `graph.facebook`, or `tourfiremai.com/api` in either new file. The module's own docstring (lines 1-8) is explicit: "no LINE API calls, no customer replies, no env reads, and no network." |
| 4 | No secrets written | ✅ PASS | `grep -nE 'sk-\|EAA\|ghp_'` finds zero literal secret-shaped strings in either file. The two `s{'k'}-…` patterns in the handler (line 68) and test (line 182) are deliberate anti-grep splits used to *describe* / *strip* the prefix — the literal `sk-` never appears in source. |
| 5 | No live LINE / OpenAI / paid-provider calls in tests | ✅ PASS | Test imports `v2.lib.admin_command_handler` + `v2.lib.admin_ops` only. No `openai`, no `requests`, no `httpx`, no `line_*` client. The supabase fixture is an in-memory fake. |
| 6 | No PDF / fee threshold change | ✅ PASS | Zero references to `fee_answer_policy`, `extract_fees`, `ondemand_vision`, `tour_fees`, `MODEL_PRICING`, `DEFAULT_THRESHOLD`, `SINGLE_SUPPLEMENT_THRESHOLD` in either new file. |
| 7 | Parser recognizes all required commands | ✅ PASS | `parse_admin_command` (lines 75-121): `help`/`?`/`commands` → help; `cases`/`case-list` → cases (or `cases_paused` if 2nd token in `{paused,pause,human_paused}`); `handoffs`/`handoff` → handoffs; `case <id>` (len≥2); `pause <id> [reason]` (len≥2); `resume <id> [reason]` (len≥2). All seven scope items present. `test_parse_supported_commands` (line 65) covers all seven explicitly. |
| 8 | Unknown/ambiguous commands return safe help, no state mutation | ✅ PASS | Unknown → `AdminCommand(action="unknown")` (line 121). Handler returns `ok=False, error="unknown_command", admin_text="ไม่เข้าใจคำสั่งนี้\n\n" + _help_text()`, default `mutated=False`. `test_unknown_command_returns_help_and_does_not_mutate` (line 172) asserts `result.mutated is False` and `bot_pauses.select_all == []`. |
| 9 | `pause <id>` uses `admin_ops.pause_bot_for_customer(...)` | ✅ PASS | Line 297-302: `pause_bot_for_customer(supabase, psid=case.psid, paused_by="admin", reason=command.reason or f"admin command by {admin_user_id}")`. `test_pause_calls_admin_ops_and_marks_paused` (line 131) asserts `conv["is_human_paused"] is True` and `conv["state"] == "human_paused"` — exactly the side-effects `admin_ops.pause_bot_for_customer` produces. |
| 10 | `resume <id>` uses `admin_ops.resume_bot_for_customer(...)` | ✅ PASS | Line 325-330: `resume_bot_for_customer(supabase, psid=case.psid, resumed_by=admin_user_id, reason=command.reason)`. `test_resume_calls_admin_ops_and_clears_paused` (line 145) asserts `conv["is_human_paused"] is False` and `conv["state"] == "collecting_preferences"`. |
| 11 | `case <id>` uses `admin_ops.get_admin_case(...)` | ✅ PASS | `_resolve_case` (line 197): tries `get_admin_case(supabase, psid=target, …)` then falls back to `get_admin_case(supabase, conversation_id=target, …)`. `test_case_detail_includes_context` (line 116) verifies the test resolves a conversation_id correctly through this fallback. |
| 12 | `cases` / `handoffs` use admin_ops listing functions | ✅ PASS | `cases` / `cases_paused` → `list_admin_cases(supabase, memory=memory, limit=_DEFAULT_LIMIT, only_open=True, only_paused=…)` (line 230). `handoffs` → `list_open_handoffs(supabase, limit=_DEFAULT_LIMIT)` (line 255). `_DEFAULT_LIMIT = 5` is documented in Dev report § 7 note 3 as intentional. |
| 13 | Admin output safe for staff LINE group (no secrets, no wholesale, PSID masked, no auto-reply text) | ✅ PASS | `_safe_text` (line 65) chains `_scrub_wholesale` → `redactor.redact` → strip residual `sk-***REDACTED***` prefix. Every visible string in `_format_case_line` / `_format_case_detail` / `_format_handoff_line` / pause-success / resume-success / case-not-found is wrapped in `_safe_text` or uses pre-masked fields from `admin_ops.AdminCaseSummary.psid_masked`. The handler never returns customer-facing text — only `AdminCommandResult.admin_text`. `test_output_redacts_secret_patterns` (line 181) and `test_output_redacts_configured_provider_names` (line 191) verify directly. Multiple `assert PSID_A not in result.admin_text` assertions across read tests. |
| 14 | Tests cover parsing / listing / case detail / pause / resume / missing target / leakage | ✅ PASS | 11 tests in 4 classes: `TestParseAdminCommand` (2), `TestReadCommands` (3: cases, handoffs, case-detail), `TestMutatingCommands` (3: pause, resume, missing-target), `TestLeakageSafety` (3: unknown→help, secret pattern, wholesale pattern). One small gap surfaced as `🟡 M1` below. |
| 15 | Broad non-live V2 suite passes / skips justified | ⚠️ NOT INDEPENDENTLY VERIFIED | Dev claim: `538 passed, 11 skipped, 0 failed`. Cannot run pytest here — only 4 files are mounted; `conftest.py`, `redactor.py`, `response_writer.py`, `state_machine.py`, `memory.py` are missing. The 538 count is plausible: prior known baseline was 501 after `DEV-2026-05-19-003-cleanup`, plus 33 admin_ops tests from `DEV-2026-05-19-004` plus 11 here ≈ 545; minor cleanup easily explains the 538. Codex should run the full suite once on a real clone as a post-merge sanity check. |

---

## 4. Findings by Severity

### 🔴 Critical (blocks GO)
None.

### 🟠 High
None.

### 🟡 Medium (worth fixing before the next ticket wires this into a real adapter)

- **M1 — Pause/resume admin_text redaction not explicitly tested.** Dev report § 5 admits: *"after the final display-name redaction tweak, the resumed Codex runtime no longer had `pytest` installed, so the last re-check was a syntax/compile pass. The code delta after the full pytest run was limited to routing pause/resume display names through `_safe_text()`."* The post-tweak code at lines 303 and 331 (`case_name = _safe_text(case.display_name or case.psid_masked)`) is correct on inspection, but no test asserts that a wholesale-shaped or secret-shaped display name is redacted in the **pause** admin_text or the **resume** admin_text specifically. The existing `test_output_redacts_secret_patterns` and `test_output_redacts_configured_provider_names` only exercise the `cases` and `case <id>` paths. Recommend two trivial follow-up tests, mirroring those, against pause and resume.
- **M2 — No authorization layer; must be enforced at the adapter.** `handle_admin_command(...)` accepts any `admin_user_id` and performs the requested mutation. This is correct for a deterministic *core*, but a future LINE webhook adapter MUST verify the caller is on a staff allow-list before forwarding text to this function. Recommend adding a short docstring note at line 204 — something like: *"Authorization (e.g. LINE userId must be in the staff allow-list) is the caller's responsibility. This function will execute any well-formed command for any `admin_user_id`."* — to lock that contract in for the next implementer.

### 🟢 Low / informational

- **L1 — `admin_user_id` is persisted raw to `bot_pauses.resumed_by` and into the fallback `reason` string.** Line 301 (`reason=command.reason or f"admin command by {admin_user_id}"`) and line 328 (`resumed_by=admin_user_id`). These columns are admin-only and never surfaced to customers, so raw values are acceptable. Worth a one-line audit if `admin_user_id` could carry sensitive content later (e.g., if it ever holds a session token instead of a stable user id).
- **L2 — Asymmetric audit trail on `paused_by` vs `resumed_by`.** `pause_bot_for_customer` is called with `paused_by="admin"` (literal), and the actual admin identity is captured in `reason`. `resume_bot_for_customer` is called with `resumed_by=admin_user_id` directly. Functional consequence: queries against `bot_pauses.paused_by` won't distinguish admins, but queries against `bot_pauses.resumed_by` will. This is fine — `admin_ops.pause_bot_for_customer` constrains `paused_by ∈ {admin, system, rule}` (admin_ops.py line 370), so this asymmetry is by design — but worth noting in case dashboard work expects a single audit-trail field.
- **L3 — `_safe_text` line 68 is format-coupled to `redactor.redact`.** The strip `out.replace(f"s{'k'}-***REDACTED***", "***REDACTED***")` assumes `redactor.redact` produces output of the form `sk-***REDACTED***` when it masks an API-key-shaped string. If `redactor.redact`'s replacement template ever changes, this strip silently becomes a no-op. A small test that asserts the post-strip text starts with `***REDACTED***` (rather than `sk-***REDACTED***`) would lock the format down.
- **L4 — `_DEFAULT_LIMIT = 5` is hard-coded.** Dev report § 7 note 3 acknowledges this; pagination is intentionally out of scope. No action needed for this task.
- **L5 — Help/admin_text is Thai-only.** Dev report § 7 note 2 acknowledges this; Thai aliases are deferred to a future task. No action needed.
- **L6 — Cowork QA workspace is partially mounted.** Only the four files listed in the prompt + `admin_ops.py` are visible. `conftest.py`, `redactor.py`, `response_writer.py`, `state_machine.py`, `memory.py`, and the rest of `v2/tests/` are absent. This makes pytest unrunnable from here. The Codex / Tiw dev machine should run `PYTHONPATH=. pytest v2/tests -q` on the branch tip as a post-QA sanity check. (Same recurring environment limitation flagged in prior QA sessions today.)

---

## 5. Tests Verified

**Read but not executed.** Static review of `v2/tests/test_admin_command_handler.py`:

| Class | Test | Asserts | Verdict on assertion correctness |
|-------|------|---------|----------------------------------|
| TestParseAdminCommand | `test_parse_supported_commands` | All 7 commands parse with correct `(action, target, reason)` | ✅ correct mapping vs. parser |
| TestParseAdminCommand | `test_parse_whitespace_and_unknown_safely` | Extra whitespace is stripped; Thai unknown returns `action="unknown"`, `target=None` | ✅ matches parser behavior |
| TestReadCommands | `test_cases_lists_safe_admin_lines` | `cases` succeeds, contains `เคสล่าสุด`, contains display name, does NOT contain raw PSID, contains masked PSID prefix `1234` | ✅ exercises `list_admin_cases` + `_format_case_line` + `_safe_text` |
| TestReadCommands | `test_handoffs_lists_open_handoffs` | `handoffs` succeeds, contains `Handoffs`, contains trigger type, does NOT contain raw PSID | ✅ exercises `list_open_handoffs` + `_format_handoff_line` |
| TestReadCommands | `test_case_detail_includes_context` | `case <conv_id>` succeeds, shows display name, tour name, tour_code_real, does NOT contain raw PSID | ✅ exercises `_resolve_case` fallback + `_format_case_detail` |
| TestMutatingCommands | `test_pause_calls_admin_ops_and_marks_paused` | `pause` succeeds, `mutated=True`, conversation row flips `is_human_paused=True`, state → `human_paused` | ✅ exercises `pause_bot_for_customer` and orchestrator pause-guard side-effects |
| TestMutatingCommands | `test_resume_calls_admin_ops_and_clears_paused` | `resume` succeeds, `mutated=True`, conversation row flips `is_human_paused=False`, state → `collecting_preferences` | ✅ exercises `resume_bot_for_customer` |
| TestMutatingCommands | `test_missing_target_does_not_create_pause` | `pause <unseen_psid>` → `ok=False`, `error=case_not_found`, `bot_pauses.select_all == []` | ✅ verifies the missing-target safety invariant |
| TestLeakageSafety | `test_unknown_command_returns_help_and_does_not_mutate` | Unknown Thai → `error=unknown_command`, `mutated=False`, `bot_pauses` empty | ✅ |
| TestLeakageSafety | `test_output_redacts_secret_patterns` | Seeds a customer with an `sk-...`-shaped name; `cases` admin_text contains neither the key nor the `sk-` prefix | ✅ note `test_..._redacts_secret_patterns` only covers the `cases` path; pause/resume paths not directly tested — see `🟡 M1` |
| TestLeakageSafety | `test_output_redacts_configured_provider_names` | Monkeypatches a wholesale-blacklist token; `case <conv_id>` admin_text replaces the token with `***WHOLESALE-REDACTED***` | ✅ note only `case <id>` is exercised — see `🟡 M1` |

Dev's reported counts:
- `pytest v2/tests/test_admin_command_handler.py` → **11 passed** — matches the 11 tests I counted.
- `pytest v2/tests/test_admin_ops.py v2/tests/test_admin_command_handler.py` → **44 passed** — implies 33 admin_ops tests, which is consistent with the 603-line `test_admin_ops.py` and the prior `QA-2026-05-19-004` `GO` verdict.
- Broad non-live V2 suite → **538 passed, 11 skipped, 0 failed**. Cannot run from this workspace; plausible against the 501 baseline. ⚠️ Recommend Codex run the full suite once on a real clone after this report lands.

---

## 6. Remaining Risks

### New risks surfaced by this report
- **R-new-1.** `M1` — Two trivial test gaps on pause/resume redaction. Risk class: regression-only. Mitigation: add two short tests mirroring `test_output_redacts_secret_patterns` against pause and resume admin_text.
- **R-new-2.** `M2` — The future LINE webhook adapter MUST enforce a staff allow-list before calling `handle_admin_command(...)`. The core does no auth on purpose; this is a contract that needs to be locked in at the adapter layer or someone could pause/resume any case via the adapter.

### Carried forward (not introduced by this task)
- **R-prior-1.** Phase 2 real-corpus accuracy on `d0a43bf+` still unmeasured. Unrelated to this PR; tracked under the separate `S4-LIVE-QA-2026-05-18-001` `NO-GO` (still in force pending a documented live re-run).
- **R-prior-2.** Real paid OCR provider (Mistral) still a stub. Unrelated to this PR.
- **R-prior-3.** Cowork QA workspace remains partially mounted (only files Codex lists are visible). Workable for targeted code QA like this one; not workable for broad-suite verification. See `L6`.

---

## 7. Next Recommended Step

Since the verdict is `GO_WITH_NOTES`:

**For Codex (Controller):**

1. **Accept this verdict.** Flip `AGENT_STATUS.json` to `QA_GO_WITH_NOTES`, append `TASK_LOG.md` with `QA-2026-05-19-005` result (verdict, report path, latest QA status commit) per the existing append-only convention. Commit both QA artifacts + push to `v2/s4-followup-vision-ondemand` (workspace has no `.git`; Codex performs the push as agreed).
2. **Run `PYTHONPATH=. pytest v2/tests --ignore=v2/tests/integration --ignore=v2/tests/live --ignore=v2/tests/test_live_openai_health.py -q`** once on a real clone of the branch tip. Dev's claimed 538/11/0 needs one out-of-Cowork confirmation since this QA session couldn't run pytest itself.
3. **Schedule the two follow-up tests** under `M1` (pause-path redaction + resume-path redaction) — either bundle them with the next admin-adapter ticket or as a tiny grooming task. Each is 5-10 lines.
4. **For the next ticket** (LINE admin adapter), make staff-allow-list authorization an explicit acceptance criterion (`M2`).

**For Tiw:**
- No business or code action required for this task. The work is staging-only, V1 untouched, Make.com untouched, no deploy, $0 spend.

**For QA (this session):**
- Stopped. Read-only. No code modified, no runtime behavior changed, no V1 / Make.com / production webhook / deploy / secret touched, no live LINE / OpenAI / paid-provider call.
- Files written: `docs/tasks/QA_REPORT_CURRENT.md` (this file) and `docs/tasks/AGENT_STATUS.json`.
- No `git commit` / `git push` from this workspace per the explicit instruction; Codex will commit and push the QA artifacts.

---

**Stopped.** Awaiting Codex to commit the QA artifacts and either approve the next ticket or address the two `🟡` follow-ups in a grooming pass.
