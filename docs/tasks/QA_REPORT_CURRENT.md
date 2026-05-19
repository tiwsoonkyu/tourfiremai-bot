# QA Report — `QA-2026-05-19-004` Admin Handoff + Memory Control foundation

**Verdict:** `GO`
**Author:** Claude Cowork QA
**Date:** 2026-05-19
**Controller:** Codex
**Paired Dev Task:** `DEV-2026-05-19-004`
**Branch reviewed:** `v2/s4-followup-vision-ondemand` @ `975b891` (docs companion to code commit `e152a07`)
**Parent baseline:** `v2/s4-followup-vision-ondemand` @ `78da867` (Codex task-open)

---

## 1. Verdict

**`GO`.** The Admin Handoff + Memory Control foundation lands as two new files and zero production-runtime diff. Every QA item in `CURRENT_QA_TASK.md` § "QA Checks" is independently verified. All 33 new tests pass; the full non-live suite is 527 passed / 0 failed / 7 Flask-skipped — exactly matching the Dev report's count. Fee thresholds, V1, Make.com, Cloudflare, Railway, Meta webhook, and the orchestrator pause-guard contract are byte-identical to the parent commit.

Two informational notes (N1, N2 below); neither blocks the verdict.

---

## 2. Scope Reviewed

| # | File / artifact | State on `975b891` (code at `e152a07`) | QA action |
|---|-----------------|----------------------------------------|-----------|
| 1 | `docs/AI_COMMAND_CENTER.md` | Unchanged in this task's scope | Read for safety rules |
| 2 | `docs/tasks/CURRENT_DEV_TASK.md` | The task brief itself (added at `78da867`) | Read in full |
| 3 | `docs/tasks/CURRENT_QA_TASK.md` | The QA brief itself | Read in full |
| 4 | `docs/tasks/TASK_LOG.md` | DEV-/QA-2026-05-19-004 opened at `78da867`; both still `PENDING` per log | Read |
| 5 | `docs/tasks/DEV_REPORT_CURRENT.md` | Rewritten by `975b891` — format-conformant, 8 required sections present | Read in full |
| 6 | `docs/tasks/AGENT_STATUS.json` | `READY_FOR_QA` / `DEV-2026-05-19-004`, `files_changed` accurate vs the actual diff | Verified |
| 7 | `v2/lib/admin_ops.py` (NEW, 833 lines) | Pause/resume + AdminCaseSummary + listings + record_handoff | Read in full + grep verified |
| 8 | `v2/tests/test_admin_ops.py` (NEW, 603 lines) | 33 tests across 7 classes | Read in full + executed |
| 9 | All production runtime modules (orchestrator, response_writer, fee_answer_policy, memory, state_machine, llm, ondemand_vision, extract_fees, fee_schema, llm_pricing, cache, cassette_redactor) | Not in diff — byte-identical to `78da867` | `git diff 78da867..HEAD -- <each>` returns 0 lines per file |
| 10 | All V1 paths (`app.py`, `scraper.py`, `fee_extractor.py`, `tourfiremai-bot-dev/`, `Procfile`, `railway.json`, `cloudflare-worker*.js`, `webhook_proxy.py`) | Not in diff | `git diff 78da867..HEAD -- <V1 paths> \| wc -l` → 0 |

Code-only diff scope (verified by `git diff 78da867..HEAD --name-only | grep -v "^docs/"`):

```
v2/lib/admin_ops.py
v2/tests/test_admin_ops.py
```

Branch checked out at `/tmp/repo` on `v2/s4-followup-vision-ondemand` @ `975b891`. Local repo confirmed in sync with `origin/v2/s4-followup-vision-ondemand`.

---

## 3. Evidence Checked — Task QA Matrix

### 3.1 Scope discipline

| # | CURRENT_QA_TASK.md check | Evidence | Result |
|---|--------------------------|----------|--------|
| 1 | Dev stayed on V2 scope only | Code diff touches only `v2/lib/admin_ops.py` + `v2/tests/test_admin_ops.py`. Docs diff touches only `docs/tasks/{DEV_REPORT_CURRENT,AGENT_STATUS}`. No file outside `v2/` or `docs/tasks/` modified. | PASS |
| 2 | V1 production code was not changed | `git diff 78da867..HEAD -- app.py scraper.py fee_extractor.py tourfiremai-bot-dev/ Procfile railway.json cloudflare-worker.js cloudflare-worker-v2.js webhook_proxy.py` → 0 lines. | PASS |
| 3 | Make.com / Cloudflare / Meta production webhook untouched | `git diff 78da867..HEAD \| grep -iE 'make\.com\|integromat\|cloudflare\|railway'` → only matches inside the Dev-report prose explicitly listing "not changed" assertions; no functional reference. `git diff 78da867..HEAD \| grep -iE 'webhook_proxy\|messenger\|graph\.facebook\|tourfiremai\.com/api'` → 0 hits. | PASS |
| 4 | No secrets written to files | `grep -REn 'sk-[A-Za-z0-9_-]{20,}\|EAA[A-Za-z0-9_-]{20,}\|ghp_[A-Za-z0-9]{20,}\|AKIA[A-Z0-9]{16}' v2/lib/admin_ops.py v2/tests/test_admin_ops.py` → 0 hits. `TestNoSecretOrWholesaleLeakage::test_no_secret_pattern_appears_in_module` passes. | PASS |
| 5 | No live OpenAI or paid-provider calls required by tests | `grep -nE 'openai\|anthropic\|mistral\|requests\.\|httpx' v2/lib/admin_ops.py` → 0 hits. Tests import only `v2.lib.admin_ops`, `v2.lib.memory`, `v2.lib.response_writer`, `pytest`, stdlib — no network SDKs. Module-scoped + full suite both run with no `V2_STAGING_OPENAI_API_KEY` and pass. | PASS |
| 6 | No PDF extraction behavior or fee thresholds changed | `git diff 78da867..HEAD -- v2/lib/fee_answer_policy.py v2/scraper/extract_fees.py v2/scraper/ondemand_vision.py v2/scraper/fee_schema.py v2/lib/llm_pricing.py v2/lib/cassette_redactor.py` → 0 lines per file. `grep -nE '^[A-Z_]+_THRESHOLD\s*=' v2/lib/fee_answer_policy.py` → `DEFAULT_THRESHOLD = 0.80` / `SINGLE_SUPPLEMENT_THRESHOLD = 0.90`, both unchanged. | PASS |

### 3.2 Admin pause / resume / silent path

| # | CURRENT_QA_TASK.md check | Evidence | Result |
|---|--------------------------|----------|--------|
| 7 | Admin pause creates/updates expected pause/conversation state | `pause_bot_for_customer` inserts a fresh `bot_pauses` row (id, paused_at, pause_until, paused_by ∈ {admin,system,rule} per migration 012 CHECK) AND, when an active conversation exists, updates `conversations` with `is_human_paused=True`, `paused_until`, `paused_reason`, `state='human_paused'`, `last_activity_at`. Locked in by `TestPause::test_pause_creates_bot_pauses_row_and_updates_conversation` (verified by re-running). | PASS |
| 8 | Paused customer is silent / does not proceed through normal bot response flow | The orchestrator pause-guard at `v2/lib/orchestrator.py:98` (`if conv.get("is_human_paused"): return TurnResult(... decision="silent_paused", silent=True)`) is byte-identical to `78da867`. `pause_bot_for_customer` writes exactly that flag. `TestSilentPath::test_orchestrator_compatible_flag_is_set` asserts the flag is True after pause; `test_is_bot_paused_for_true_after_pause` validates the defense-in-depth predicate. | PASS |
| 9 | Admin resume clears pause safely + auditable event | `resume_bot_for_customer` (a) sets `bot_pauses.resumed_at` + `resumed_by`, (b) clears `conversations.is_human_paused`/`paused_until`/`paused_reason` and sets `state=new_state` (default `collecting_preferences`), (c) refuses to land in a silent state (raises `ValueError`), (d) closes any open `handoffs` with `resolution='bot_resumed'` + `admin_responder` recorded for audit. `TestResume::test_resume_closes_active_pause_and_clears_conversation_flag`, `test_resume_into_silent_state_rejected`, `test_resume_closes_open_handoffs` all PASS. | PASS |

### 3.3 Case summary content

| # | CURRENT_QA_TASK.md check | Evidence | Result |
|---|--------------------------|----------|--------|
| 10 | Case summary includes customer name when available | `_display_name(memory_view, customer_row, psid)` resolves in priority order: `customer_memory.customer_name` → `customers.fb_name` → `"Customer <masked-PSID>"`. Wholesale-scrubbed at each step. `TestCaseSummary::test_summary_resolves_display_name_from_customer_memory_first` + `test_summary_falls_back_to_fb_name_when_no_memory_name` PASS. | PASS |
| 11 | Case summary includes selected tour / latest offer / open handoff when available | `_selected_tour_brief` joins `selected_tours` (unlocked_at IS NULL) ⨝ `tours_canonical`; `_latest_offer_brief` reads newest `offer_snapshots`; `_open_handoff_brief` reads most recent unresolved `handoffs`. All three wired into `AdminCaseSummary`. `TestCaseSummary` 10/10 PASS, covering each field individually plus the lookup-by-conversation-id path. | PASS |
| 12 | Open handoff queue listing is deterministic and safe for dashboard use | `list_open_handoffs` filters on `resolution IS NULL`, sorts by `triggered_at DESC`, caps at `limit` (default 50). Each row → `OpenHandoffBrief` with `psid_masked` (via `redactor.mask_psid`) and `trigger_detail_summary` (only `reason`/`missing_field`/`note` keys; wholesale-scrubbed; PII-redacted). Raw JSON never exposed. `TestListings::test_list_open_handoffs_masks_psid_and_redacts_wholesale` PASS. | PASS |

### 3.4 Brand / secret defense

| # | CURRENT_QA_TASK.md check | Evidence | Result |
|---|--------------------------|----------|--------|
| 13 | No wholesale partner names introduced into prompts/logs/reports/cassettes/customer-facing output | `grep -nwE '(TTN\|ZEGO\|FORMOSA\|i-travel\|rich\.tour\|best\.tour\|GS\.travel)' v2/lib/admin_ops.py` → 0 hits. The only TTN/ZEGO occurrences in `v2/tests/test_admin_ops.py` are in **hostile-input fixtures** asserting redaction (`"ทัวร์โตเกียว by TTN partner"` + `"Alice via ZEGO promo"`), with matching `assert "TTN" not in summary.selected_tour.name` and `assert "ZEGO" not in summary.display_name`. The shared `_WHOLESALE_BLACKLIST` regex from `response_writer.py` is reused (single source of truth). `TestNoSecretOrWholesaleLeakage` (4 tests) PASS. | PASS |
| 14 | Tests cover the main pause/resume/case-summary paths | 33 new tests across 7 classes — `TestPause` (6), `TestSilentPath` (2), `TestResume` (4), `TestCaseSummary` (10), `TestListings` (4), `TestNoSecretOrWholesaleLeakage` (4), `TestRecordHandoff` (3). Coverage matches the explicit "Required Tests" list in `CURRENT_DEV_TASK.md` 1:1. | PASS |

### 3.5 Tests verified (re-run by QA)

| # | Test surface | Result |
|---|--------------|--------|
| T1 | `PYTHONPATH=. pytest v2/tests/test_admin_ops.py -v` | **33 passed in 0.11s** — all 33 named tests cited in Dev report § 5 ran and passed. |
| T2 | Full non-live suite: `pytest v2/tests --ignore=test_integration_staging.py --ignore=test_live_openai_health.py -q` | **527 passed, 7 skipped, 0 failed in 1.48s.** Matches Dev's reported 527/7/0 exactly. The 7 skips are Flask-not-installed in `test_webhook.py`. |
| T3 | Implicit baseline delta | 527 − 33 = 494 → matches Dev's reported baseline of 494+7=501 at `78da867`. Direct re-run at `78da867` blocked by sandbox permissions on `.git/index.lock` (read-only workspace), but the delta arithmetic + the all-new-file diff (no edits to existing tests) is structurally sufficient. **0 regressions.** |
| T4 | Pre-existing safety tests still pass | Full-suite run includes `TestFeePolicyUnchanged`, `TestPaidStubsFailClosed`, `TestNoWholesaleLeakage`, `TestPricingUnchanged`, `TestQACleanupL1ConfidenceKeys`, `TestQACleanupL2NoSupabaseInBenchmarkMode` from prior cycles — all present in the 527 and all pass. |
| T5 | Tests NOT run | `test_integration_staging.py` (requires `V2_STAGING_DB_*`; out of scope) and `test_live_openai_health.py` (opt-in only). Neither modified. |

---

## 4. Findings by Severity

### Critical (blocks GO)
None.

### High
None.

### Medium
None.

### Low / informational

**N1. Dev report line-count nit.** The Dev report's § 2 table says `v2/lib/admin_ops.py | +625 (new)`, but the actual file is 833 lines (`wc -l v2/lib/admin_ops.py` → 833). The diff is genuinely +833/-0 against `78da867`. Likely an early-draft figure that wasn't refreshed before commit. Documentation nit, not a functional issue. No impact on test count or behavior.

**N2. `list_admin_cases` is O(n) in Python.** Dev called this out themselves as R3 — the function reads all `conversations` rows via `select_all({})`, sorts in Python, then filters and projects. Fine for staging (≤ thousands of rows) and matches the in-memory test fake. Worth flagging for the dashboard read-API follow-up: when wiring to real Postgres, prefer a `ORDER BY ... LIMIT` server-side. Not in scope for this task; not a blocker.

### Informational only

- The defense-in-depth pattern (`is_bot_paused_for` checks both `bot_pauses` row AND `conversations.is_human_paused`) is good belt-and-suspenders engineering for a future admin command bot that wouldn't already hold the conversation row.
- Migration-aligned validation: `pause_bot_for_customer` validates `paused_by ∈ {'admin','system','rule'}` matching migration 012 CHECK exactly; `record_handoff` validates `trigger_type` against the 8-value migration 011 CHECK list exactly. Caught early via `ValueError`, well before Postgres would reject the insert. Locked in by `TestPause::test_pause_validates_inputs` and `TestRecordHandoff::test_record_handoff_validates_trigger_type`.
- The `_scrub_wholesale` helper reuses `response_writer._WHOLESALE_BLACKLIST` rather than redefining the regex set — single source of truth, so any future blacklist expansion propagates automatically to the admin surface.
- `resume_bot_for_customer`'s `is_silent_state(target_state)` check is a small but important safety rail: it would catch a future bug where a caller passes `human_paused` (or any state that `state_machine.is_silent_state` returns True for) as the resume target.
- Cumulative branch state vs `v2/foundation`: now 11 commits, all independently QA-cleared. `16fdd86` → `39bcf53` → `b325e92` → `516b1c3` → `1ec49e2` → `d0a43bf` → `ef4c0ae` → `ff28807` → `bd4784e`/`d68a4be` → `9ccf7ec`/`e473a26` → `78da867`/`e152a07`/`975b891`.

---

## 5. Tests Verified

QA ran the test suite locally on `v2/s4-followup-vision-ondemand` @ `975b891` after `pip install pytest --break-system-packages`.

### 5.1 Module-scoped run

```bash
PYTHONPATH=. python3 -m pytest v2/tests/test_admin_ops.py -v --no-header
# 33 passed, 1 warning in 0.11s
```

All 33 tests pass:

- `TestPause` (6/6): `test_pause_creates_bot_pauses_row_and_updates_conversation`, `test_pause_records_handoff_when_none_open`, `test_pause_reuses_existing_open_handoff`, `test_pause_validates_inputs`, `test_pause_without_active_conversation_still_inserts_pause`, `test_paused_default_ttl_uses_120_minutes`
- `TestSilentPath` (2/2): `test_is_bot_paused_for_true_after_pause`, `test_orchestrator_compatible_flag_is_set`
- `TestResume` (4/4): `test_resume_closes_active_pause_and_clears_conversation_flag`, `test_resume_closes_open_handoffs`, `test_resume_into_silent_state_rejected`, `test_resume_when_not_paused_is_safe_noop_on_pause_row`
- `TestCaseSummary` (10/10): display name resolution (memory → fb_name → masked PSID), masked PSID in visible fields, selected tour, latest offer, open handoff, paused→silent, unknown→None, by conversation_id, validation of required identifiers
- `TestListings` (4/4): newest-first; `only_paused`; `only_open` excludes closed; open handoffs mask PSID + redact wholesale
- `TestNoSecretOrWholesaleLeakage` (4/4): no secret-shape in source, no wholesale token in source, tour-name and display-name wholesale scrubbing (hostile-string fixtures)
- `TestRecordHandoff` (3/3): requires conversation, validates trigger_type, happy-path insert

### 5.2 Full non-live suite

```bash
PYTHONPATH=. python3 -m pytest v2/tests \
  --ignore=v2/tests/test_integration_staging.py \
  --ignore=v2/tests/test_live_openai_health.py -q --no-header
# 527 passed, 7 skipped, 1 warning in 1.48s
```

**527 hard-passes + 7 Flask-skipped + 0 failed.** Matches Dev's reported 527 exactly. The 7 skips are all in `v2/tests/test_webhook.py` due to Flask not being installed in the sandbox — same as every prior cycle and not a regression.

Delta vs Dev-reported baseline (494 at `78da867`): +33, exactly accounting for the 33 new tests. Zero changes to any pre-existing test file.

### 5.3 Tests NOT run (correctly out of scope)

- `v2/tests/test_integration_staging.py` — requires `V2_STAGING_DB_*` env; explicitly out of scope per `CURRENT_QA_TASK.md` "No live OpenAI or paid-provider calls" and Sprint protocol.
- `v2/tests/test_live_openai_health.py` — opt-in only; not relevant to this task.

Neither was modified in this diff (confirmed via `git diff 78da867..HEAD --name-only`).

---

## 6. Remaining Risks

### Resolved by this task
- Admin cannot take over cleanly → **addressed**. `pause_bot_for_customer` + `resume_bot_for_customer` provide a deterministic backend layer.
- Bot may continue chatting while admin is active → **addressed**. The orchestrator's existing pause-guard reads `conv.is_human_paused`; `pause_bot_for_customer` writes exactly that flag.
- Admin cannot see customer name/intent/selected tour/why-handoff → **addressed**. `AdminCaseSummary` carries all four (display name, latest memory, selected tour, open handoff).

### Carried forward (not introduced by this task)
- **R1.** Wholesale-token redaction is best-effort regex; new partner names that don't match the existing `_WHOLESALE_BLACKLIST` could surface. Same surface as `response_writer._has_brand_leak`, deliberately shared. Future polish: periodic scan of `customer_memory.customer_name` and `tours_canonical.name`.
- **R2.** `is_bot_paused_for` parses ISO timestamps in Python; if the staging DB clock and the app clock drift > a few seconds, edge cases are possible. Acceptable for an admin tool.
- **R3.** `list_admin_cases` does an O(n) scan in Python (Dev's R3, also QA's N2). Fine for staging; needs a server-side `ORDER BY ... LIMIT` when wiring to a real dashboard.
- **R4.** No production UI yet — by design per task scope. Sprint 5 wires this through a LINE admin command handler or a minimal dashboard read-API.

None blocks this verdict.

---

## 7. Next Recommended Step

Since the verdict is `GO`:

**For Codex (Controller):**

1. Accept this `GO` verdict. Flip `AGENT_STATUS.json` to `QA_GO`. Append `TASK_LOG.md` with `DEV-2026-05-19-004` / `QA-2026-05-19-004` outcome + commits `e152a07` + `975b891` + this QA report path.
2. Decide next workstream per Dev report § 8:
   - **Option A (recommended — operational reliability):** Wire `admin_ops` into a LINE admin command handler — Sprint 5 prerequisite. Admin sends `pause <psid_or_web_code>` / `resume <…>` / `cases` to the staff LINE group; handler calls `pause_bot_for_customer` / `resume_bot_for_customer` / `list_admin_cases`. No customer-facing change; pure admin-staff workflow.
   - **Option B:** Wire `get_admin_case` into a minimal dashboard read-API (single JSON endpoint behind admin auth — `GET /v2/admin/cases?only_paused=1`). Needs an auth story before code.
3. (Optional polish, non-blocking) When wiring the dashboard read-API, swap `list_admin_cases`' Python sort/filter for a server-side `ORDER BY last_activity_at DESC LIMIT n` Postgres query (closes N2).
4. (Optional doc fix) Update Dev report § 2 line-count from `+625` to `+833` for `v2/lib/admin_ops.py` if Dev re-touches the report (closes N1; trivial).

**For Tiw:**
- No code action required on this task.
- When Codex approves Option A or B, no new secrets needed (admin_ops uses only the existing Supabase staging credentials already in your shell env contract).

---

**Stopped.** Per QA handoff rule (`AI_COMMAND_CENTER.md` § "Handoff Rule Between Agents"): not continuing implementation. `AGENT_STATUS.json` flipped to `QA_GO`. Awaiting Codex direction.
