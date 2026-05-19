# Dev Report — `DEV-2026-05-19-004` Admin Handoff + Memory Control foundation

**Status:** `READY_FOR_QA`
**Verdict recommendation to QA:** **`GO`**
**Author:** Claude Cowork Dev
**Date:** 2026-05-19
**Branch:** `v2/s4-followup-vision-ondemand` (pushed)
**Parent commit:** `78da867` (Codex's task-open)
**Spend this session:** **$0.00** — no live OpenAI, no paid-provider, no live cassette recording, no Make.com, no production deploy.

---

## 1. Status

`READY_FOR_QA`. New module `v2/lib/admin_ops.py` + paired test module `v2/tests/test_admin_ops.py` land cleanly. Zero changes to any production runtime module, zero changes to fee thresholds, zero changes to V1. Full non-live V2 suite goes from 494 passed → **527 passed** (+33 = exactly the new tests).

---

## 2. Files Changed

```
v2/lib/admin_ops.py         | +625  (new — admin pause/resume + case summary + listings)
v2/tests/test_admin_ops.py  | +603  (new — 33 tests, in-memory fakes only)
docs/tasks/DEV_REPORT_CURRENT.md | rewritten with this report
docs/tasks/AGENT_STATUS.json     | flipped to READY_FOR_QA
2 code files added, 2 docs files updated.
```

**NOT changed (verified by `git diff HEAD -- ... | wc -l` returning 0):**

- `v2/lib/orchestrator.py`, `v2/lib/response_writer.py`, `v2/lib/fee_answer_policy.py`,
  `v2/lib/memory.py`, `v2/lib/state_machine.py`, `v2/lib/llm.py`,
  `v2/scraper/ondemand_vision.py`, `v2/scraper/extract_fees.py`.
- Any V1 path (`app.py`, `scraper.py`, `fee_extractor.py`, `tourfiremai-bot-dev/`,
  `Procfile`, `railway.json`, `cloudflare-worker.js`).
- Any Make.com / Cloudflare / Railway / Meta webhook / public webhook file.
- Any migration (no schema change needed — `bot_pauses` (012) + `handoffs` (011) + `conversations.is_human_paused` (003) already provide the fields).
- Any secret store, env contract, or `.env` template.

---

## 3. Root Cause / Business Need

V1 testing surfaced four operational pain points that PDF accuracy work alone does not fix:

1. The bot forgot context after delay → already addressed by Sprint 1 memory.
2. The bot asked again after the customer had already selected a tour → already addressed by Sprint 1 `selected_tours` lock.
3. **Admin could not take over cleanly** → unfixed before this task.
4. **The bot could keep chatting while an admin was active** → fast-path guard exists in `orchestrator.py` (`conv.is_human_paused`), but nothing in the codebase actually *set* that flag from an admin action.
5. **Admin had no view-model** of who needs attention, what the customer asked for, and which tour they selected.

This task ships the deterministic backend layer that a future dashboard or LINE admin command handler can call to do (3) and (4) safely, plus an `AdminCaseSummary` for (5). No UI in this task — the scope explicitly says "Do not create a production UI in this task unless it is trivial and fully tested" and "A JSON/view-model layer is enough".

---

## 4. Summary of Changes

### 4.1 New module `v2/lib/admin_ops.py`

Public surface (re-exported via `__all__`):

| Function | Purpose |
|----------|---------|
| `pause_bot_for_customer(supabase, *, psid, paused_by, reason, ttl_minutes=120, record_handoff_if_missing=True, handoff_trigger_type='human_request')` | Inserts a `bot_pauses` row, updates `conversations.is_human_paused=True` and `state='human_paused'`, optionally registers a `handoffs` row when none is open. Returns `PauseResult`. |
| `resume_bot_for_customer(supabase, *, psid, resumed_by, reason, new_state='collecting_preferences', close_open_handoffs=True, handoff_resolution='bot_resumed')` | Marks the active `bot_pauses` row resumed, clears the conversation pause flag, sets state to a non-silent target, closes any open `handoffs` rows with `resolution='bot_resumed'`. Refuses to resume INTO a silent state. Returns `ResumeResult`. |
| `is_bot_paused_for(supabase, psid)` | Defense-in-depth predicate. True if either an active `bot_pauses` row with `pause_until` in the future exists, OR `conversations.is_human_paused=True`. |
| `get_admin_case(supabase, *, psid=None, conversation_id=None, memory=None)` | Builds an `AdminCaseSummary` for one customer/conversation. Returns `None` if nothing exists for the identifier. |
| `list_admin_cases(supabase, *, memory=None, limit=50, only_open=True, only_paused=False)` | Newest-first list, with `only_paused` filter. |
| `list_open_handoffs(supabase, *, limit=50)` | Open (`resolution IS NULL`) handoffs, newest first, with PSIDs masked and `trigger_detail` summarised. |
| `record_handoff(supabase, *, psid, trigger_type, trigger_detail=None, conversation_id=None)` | Direct surface for callers that need a handoff row without pausing. Validates `trigger_type` against the migration-011 CHECK list. |

Data classes (also re-exported):

- `AdminCaseSummary` — view-model carrying display name, masked PSID, conversation state, latest memory fields (country/city/budget/pax/month/airline), `SelectedTourBrief`, `LatestOfferBrief`, `OpenHandoffBrief`.
- `SelectedTourBrief` — locked tour projection: web_code, tour_code_real, name (wholesale-scrubbed), price, selected_at, booking_status, is_fee_acknowledged.
- `LatestOfferBrief` — latest `offer_snapshots` row: id, presented_at, tour_count, top tour web_code/name/price, was_selected/selected_rank.
- `OpenHandoffBrief` — open handoff projection: id, masked PSID, conversation_id, triggered_at, trigger_type, short trigger_detail_summary.
- `PauseResult`, `ResumeResult` — caller-facing return values with redacted PSIDs.

Constants: `DEFAULT_PAUSE_TTL_MINUTES = 120`, `MAX_PAUSE_TTL_MINUTES = 24 * 60`.

### 4.2 Wholesale + secret defense in depth

Every free-text field that crosses the admin boundary is scrubbed:

1. `_scrub_wholesale(text)` replaces any match of the shared
   `v2.lib.response_writer._WHOLESALE_BLACKLIST` regex with
   `***WHOLESALE-REDACTED***`. Single source of truth — same regex used by the
   customer-facing response writer.
2. Trigger-detail JSON from `handoffs.trigger_detail` is never echoed raw — it
   is summarised to a short `key=value; key=value` string and then passed
   through `redactor.redact` (PSID/token/email/phone masking).
3. PSIDs in `OpenHandoffBrief` and inside log records are masked via
   `redactor.mask_psid`. The summary preserves the raw PSID in
   `AdminCaseSummary.psid` so an authorised admin can copy it to the support
   tool — `psid_masked` is the field intended for human-readable rendering.

### 4.3 Orchestrator compatibility — no code change required

The existing pause-guard in `v2/lib/orchestrator.py:98`

```python
if conv.get("is_human_paused"):
    return TurnResult(... decision="silent_paused" ...)
```

continues to work unchanged. `admin_ops.pause_bot_for_customer` writes that
exact flag, so the orchestrator naturally stays silent after admin pause.
`test_admin_ops.TestSilentPath::test_orchestrator_compatible_flag_is_set`
guards this contract.

### 4.4 What we did NOT build (deliberate)

- No production UI (per task scope).
- No HTTP endpoints (no Flask routes added; the webhook layer is unchanged).
- No `notify_team_line` wiring (Sprint 5).
- No LINE admin command parser (future task; module surface is already shaped for it).
- No new migration (the existing schema is already sufficient).
- No change to `state_machine.transition` (the existing `_from_human_paused`
  + `_from_waiting_team` already cover admin_takeover / admin_resumed via
  `StateContext`; admin_ops simply writes the underlying rows so future
  orchestrator paths can flip those flags).

---

## 5. Tests Run

### Module-scoped

```bash
PYTHONPATH=. pytest v2/tests/test_admin_ops.py -v
# 33 passed in 0.08s
```

33 new tests across 7 test classes:

- **`TestPause` (6 tests)** — pause creates bot_pauses + updates conversation;
  pause records handoff when none open; pause reuses an existing open handoff;
  input validation (empty psid, bad paused_by, ttl out of range); pause with
  no active conversation still inserts a pause row; default TTL is 120 min.
- **`TestSilentPath` (2 tests)** — `is_bot_paused_for` flips True after pause;
  the conversation row's `is_human_paused` flag is set so the orchestrator's
  short-circuit stays compatible.
- **`TestResume` (4 tests)** — resume closes the active pause + clears the
  conversation flag + sets state to a non-silent target + closes open handoffs;
  refuses to resume INTO a silent state (`human_paused` / `closed`);
  no-op-safe when not currently paused.
- **`TestCaseSummary` (10 tests)** — display name resolution (memory > fb_name
  > masked PSID); masked PSID in visible fields; selected_tour resolves through
  the `selected_tours` ⨝ `tours_canonical` join; latest_offer resolves through
  `offer_snapshots`; open_handoff resolves with masked PSID; `is_silent=True`
  when paused; unknown PSID returns None; lookup by conversation_id;
  validation of required identifiers.
- **`TestListings` (4 tests)** — `list_admin_cases` newest-first ordering;
  `only_paused` filter; `only_open` excludes closed; `list_open_handoffs`
  masks PSID + redacts wholesale tokens in trigger detail.
- **`TestNoSecretOrWholesaleLeakage` (4 tests)** — module source has no
  OpenAI/Anthropic/FB-page/GitHub-token shapes; module source has no
  wholesale-brand tokens; tour-name and display-name wholesale scrubbing
  works end-to-end (hostile-string fixture).
- **`TestRecordHandoff` (3 tests)** — requires a conversation; validates
  `trigger_type` against migration-011 CHECK; happy-path insert returns id.

### Full non-live suite

```bash
PYTHONPATH=. pytest v2/tests \
  --ignore=v2/tests/test_integration_staging.py \
  --ignore=v2/tests/test_live_openai_health.py -q
# 527 passed, 7 skipped (Flask not installed), 0 failed in 2.13s
```

Baseline (parent `78da867`): 494 passed + 7 skipped = 501. After this commit:
527 passed + 7 skipped = 534. Delta = +33, exactly matching the new tests. **Zero regressions.**

### Tests NOT run

- `v2/tests/test_integration_staging.py` — requires `V2_STAGING_DB_*` env (out
  of scope; user hard rule).
- `v2/tests/test_live_openai_health.py` — opt-in; not relevant to this task.

---

## 6. Risks / Assumptions

### Assumptions

1. **The `bot_pauses` table contract from migration 012** — `CHECK (pause_until > paused_at)` and `paused_by IN ('system','admin','rule')` — matches the schema currently live on staging (last applied during Sprint 1). Verified by reading `v2/supabase/migrations/20260517_012_bot_pauses.sql`.
2. **The orchestrator's pause-guard reads `conv.get('is_human_paused')`** — this is the exact pattern in `v2/lib/orchestrator.py:98`. We write that flag explicitly in `pause_bot_for_customer` and clear it in `resume_bot_for_customer`. Verified by reading orchestrator.
3. **`MemoryService` is optional for the case summary path** — when callers don't have a `MemoryService` instance handy (e.g. a future cron job), `get_admin_case` falls back to reading `customer_memory` directly via `supabase.table(...)`. Both paths are unit-tested.
4. **The InMemorySupabase fixture in `v2/tests/conftest.py` faithfully models the production query surface for the operations admin_ops uses** — `select_one`, `select_all`, `select_latest`, `insert`, `update`. The two existing helpers `select_all` and `select_latest` are already used elsewhere in the codebase (memory tests + handoff tests via orchestrator), so we don't introduce new query patterns.

### Risks (carried forward, not introduced)

- **R1.** Wholesale-token redaction is best-effort regex; if a new partner appears whose name doesn't match the existing blacklist, the admin view could surface it. This is the same risk surface as `response_writer._has_brand_leak`, which we deliberately reuse. Future polish: cron job that scans `customer_memory.customer_name` and `tours_canonical.name` for new patterns.
- **R2.** `is_bot_paused_for` decides "active" by checking `pause_until > now()` in Python. If the staging DB clock and the app clock drift > a few seconds, edge-case race conditions are possible. Acceptable for an admin tool (admin sees the result and can re-pause).
- **R3.** `list_admin_cases` does an O(n) scan of `conversations` in memory before applying the limit. Fine for staging (≤ thousands of rows). For production scale, we'll want a DB-side ORDER BY ... LIMIT — wire a dedicated `supabase.table(...).list_paged(...)` helper in a follow-up.

### Hard rules — all respected

- ✅ V1 untouched (verified: `git diff HEAD -- app.py scraper.py fee_extractor.py tourfiremai-bot-dev/ Procfile railway.json cloudflare-worker.js` → empty).
- ✅ Make.com untouched (no references in diff).
- ✅ No deploy.
- ✅ No live OpenAI call.
- ✅ No paid-provider call.
- ✅ No production webhook touched (no changes to `v2/webhook/`).
- ✅ Fee thresholds in `fee_answer_policy.py` UNCHANGED (`DEFAULT_THRESHOLD=0.80`, `SINGLE_SUPPLEMENT_THRESHOLD=0.90` — verified by grep + `git diff` → empty).
- ✅ No customer-facing auto-reply added (the admin module does not generate any outbound message text).
- ✅ No PDF extraction behavior changed.
- ✅ No new Supabase migration / no irreversible schema change.
- ✅ No real Supabase credentials required for unit tests.
- ✅ No secrets written / committed (pre-commit grep on the new files clean — no `sk-…`, `EAA…`, `ghp_…`, `Bearer …`, JWT, or LINE token shapes).
- ✅ No wholesale brand names introduced (regex check against `_WHOLESALE_BLACKLIST` clean; redaction tests exercise the hostile-input path).

---

## 7. What QA Should Verify

| # | Check | How to verify |
|---|-------|---------------|
| 1 | Two new files only, no production-module diff | `git diff HEAD --name-only` → `v2/lib/admin_ops.py` + `v2/tests/test_admin_ops.py` + 2 docs files |
| 2 | Orchestrator pause-guard contract intact | `git diff HEAD -- v2/lib/orchestrator.py` → empty |
| 3 | Fee thresholds unchanged | `git diff HEAD -- v2/lib/fee_answer_policy.py` → empty; grep `DEFAULT_THRESHOLD = 0.80`, `SINGLE_SUPPLEMENT_THRESHOLD = 0.90` |
| 4 | Pause → conv.is_human_paused True | `TestPause::test_pause_creates_bot_pauses_row_and_updates_conversation` + `TestSilentPath::test_orchestrator_compatible_flag_is_set` |
| 5 | Resume → conv.is_human_paused False AND state non-silent | `TestResume::test_resume_closes_active_pause_and_clears_conversation_flag` |
| 6 | Resume refuses to land in a silent state | `TestResume::test_resume_into_silent_state_rejected` |
| 7 | Admin case summary resolves all required fields | `TestCaseSummary` (10 tests cover display name, masked PSID, selected tour, latest offer, open handoff, paused→silent, unknown→None, by conversation_id) |
| 8 | Open handoffs listing masks PSID + redacts wholesale | `TestListings::test_list_open_handoffs_masks_psid_and_redacts_wholesale` |
| 9 | No wholesale leak in summary outputs | `TestNoSecretOrWholesaleLeakage::test_summary_redacts_wholesale_in_tour_name` + `test_summary_redacts_wholesale_in_display_name` |
| 10 | No secret-shape in source | `TestNoSecretOrWholesaleLeakage::test_no_secret_pattern_appears_in_module` |
| 11 | Full suite passes with 0 regressions | `pytest v2/tests --ignore=test_integration_staging.py --ignore=test_live_openai_health.py -q` → 527 passed, 7 skipped, 0 failed |

---

## 8. Next Recommended Step

**For Codex:** Accept this `GO` verdict (after independent QA review). Two natural next-task options:

1. **Wire admin_ops into a LINE admin command handler** (Sprint 5 "Handoff + LINE Notify" prerequisite). Admin sends `pause <web_code_or_psid>` / `resume <…>` to the staff LINE group → `admin_ops.pause_bot_for_customer(...)` / `resume_bot_for_customer(...)`. Read-only `cases` command renders `list_admin_cases` as a short LINE bubble.
2. **Wire `admin_ops.get_admin_case` into a minimal dashboard read-API** (a single JSON endpoint behind admin auth — `GET /v2/admin/cases?only_paused=1`). Then the dashboard front-end can iterate without further backend changes.

Both are unlocked by this foundation. (1) is closer to "operational reliability" — preferred per the current `AI_COMMAND_CENTER.md` Active Priority. (2) is closer to "admin visibility" — also high priority but needs an auth story first.

**For Tiw:** No action required on this task; verdict pending QA.

---

**Stopped.** Awaiting QA / Codex review per `AI_COMMAND_CENTER.md § "Handoff Rule"`.
