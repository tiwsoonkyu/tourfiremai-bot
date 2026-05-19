# QA Report — `QA-2026-05-19-007` Page Post Intelligence Wiring (Admin Commands + Response Planning + Orchestrator)

**Reviewer:** Claude Cowork QA
**Dev task reviewed:** `DEV-2026-05-19-007`
**Branch:** `v2/s4-followup-vision-ondemand`
**Commit referenced:** `3bf63a7`
**Date:** 2026-05-19
**Spend this session:** `$0.00` — read-only review, no live Meta / FB / LINE / OpenAI / OCR / paid-provider / Supabase calls, no migration applied, no deploy, no secrets touched.

---

## 1. Verdict

`GO`

The Sprint 5 wiring of the Page Post Intelligence + Sold-Out Signal foundation
into (a) the deterministic admin command handler, (b) the response writer's
optional `planning` kwarg, and (c) the orchestrator's per-turn pipeline is
correct, scope-disciplined, and fully covered by in-memory unit tests. All 19
hard QA checks pass. No V1 / Make.com / production-webhook surfaces were
touched. The LLM is bypassed on the blocked path and only receives a compact,
wholesale-scrubbed `page_post_planning_note` on the allowed path. Migration
020 remains unapplied on staging, as required by the task's operational note.

---

## 2. Scope Reviewed

The five Codex-defined review areas were inspected end-to-end:

1. Admin command handler for `posts`, `post <id>`, `mark_full`, `mark_sold_out`,
   `clear_full`, `clear_sold_out` — `v2/lib/admin_command_handler.py`.
2. Response writer planning context, compact note injection, and deterministic
   `canned_blocked` short-circuit — `v2/lib/response_writer.py`.
3. Orchestrator planning context integration via `_build_planning_context` +
   `_resolve_planning_candidate` and the new optional `source_post_id`,
   `source_type`, `source_platform` kwargs on `handle_turn` —
   `v2/lib/orchestrator.py`.
4. Source post context handling (`get_source_context` /
   `build_response_planning_context`) — `v2/lib/page_post_context.py`.
5. Tests + scope discipline — `v2/tests/test_page_post_wiring.py`,
   `v2/tests/test_orchestrator_planning.py`, and the surrounding test suite.

---

## 3. Evidence Checked

Files read in this QA pass:

- `docs/AI_COMMAND_CENTER.md`
- `docs/V2_PAGE_POST_INTELLIGENCE_PLAN.md`
- `docs/tasks/CURRENT_QA_TASK.md`
- `docs/tasks/TASK_LOG.md`
- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`
- `v2/lib/orchestrator.py`
- `v2/lib/admin_command_handler.py`
- `v2/lib/response_writer.py`
- `v2/lib/page_post_context.py`
- `v2/tests/test_orchestrator_planning.py`
- `v2/tests/test_page_post_wiring.py`
- `v2/supabase/migrations/` listing — confirmed migration 020 present but no
  re-application attempted.

Greps confirmed:

- Wholesale brand tokens (`ttn|zego|formosa|i[-\s]?travel|rich tour|best tour`)
  appear in the new files only as (a) the `_WHOLESALE_BLACKLIST` definition
  inside `response_writer.py` and (b) test-input strings inside
  `test_page_post_wiring.py` used to verify scrubbing. No wholesale name
  appears in admin-facing or bot-facing output paths.
- `os.environ` / `os.getenv` / `getenv(` — zero hits in
  `v2/lib/page_post_context.py` and `v2/lib/admin_command_handler.py`. The
  orchestrator's only env-touching code is the pre-existing
  on-demand-fee path, unchanged in this task.
- `requests.` / `httpx.` / `openai.` / `psycopg.connect` — zero hits in
  `test_orchestrator_planning.py` and `test_page_post_wiring.py`. Both new
  test files use only the in-memory Supabase fake + `MockLLMClient`.
- V1 surfaces (`app.py`, `cloudflare-worker.js`, `make_blueprint*.json`) —
  not modified in this task (mtime predates Sprint 4 by 10+ days). No
  references appear in any V2 file changed by `DEV-2026-05-19-007`.

---

## 4. Findings by Severity

### Critical
None.

### High
None.

### Medium
None.

### Low (advisory — not blocking)

- **L1. Planning short-circuit ordering with FEE_CHECK_REQUIRED.** In
  `response_writer.write_response`, the page-post planning block at step 2.5
  is positioned AFTER the `WAITING_TEAM`, `FEE_CHECK_REQUIRED`, and
  `BOOKING_READY_FOR_HANDOFF` early-return branches. This is intentional —
  once a customer is in `fee_check_required` for a tour they already locked,
  the fee handoff is the right reply. However, if a tour gets marked `full`
  AFTER a customer has already entered `fee_check_required`, the bot will
  continue producing the fee canned reply rather than the
  `REASON_TOUR_FULL` block reason. This is a minor product-clarity edge
  case, not a recommendation-safety failure (the bot is not recommending a
  full tour — it is processing fee follow-up for an already-locked tour).
  Recommend a follow-up: when an admin marks a tour `full`, optionally
  surface that signal in the fee handoff message so the staff team can
  pivot the conversation. Not a blocker for this task.

- **L2. Single in-progress source-attribution caller.** `handle_turn` now
  accepts `source_post_id` / `source_type` / `source_platform` kwargs but
  the production webhook does not yet pass them. Until the future webhook
  source-attribution task lands, the orchestrator only blocks on
  tour-scope (and, indirectly via memory, departure-scope) overrides. The
  Dev report acknowledges this explicitly. The orchestrator's tests assert
  the post-scope path works when a future caller does pass
  `source_post_id`, so the wiring is correct — it just isn't reachable
  from real traffic yet.

- **L3. Migration 020 not yet applied on staging.** Per Codex's operational
  note in `CURRENT_QA_TASK.md`, the Supabase connector needs re-auth and
  local staging credentials are not present. This task's code wiring does
  not break in a non-applied environment (the in-memory fake mimics the
  needed shape), but real staging traffic will return `None`/empty until
  migration 020 is applied. Recommend: apply migration 020 before any
  production webhook source-attribution wiring.

---

## 5. Tests Verified

QA re-ran the full non-live V2 test suite from a clean shell to confirm
Dev's reported numbers:

| Command | Result |
|---------|--------|
| `pytest v2/tests/test_orchestrator_planning.py -q` | `4 passed in 0.08s` |
| `pytest v2/tests/test_page_post_wiring.py v2/tests/test_page_post_context.py v2/tests/test_admin_command_handler.py v2/tests/test_orchestrator.py v2/tests/test_orchestrator_planning.py v2/tests/test_response_writer.py -q` | `124 passed in 0.25s` |
| Broad non-live V2 (`pytest v2/tests/ --ignore=v2/tests/test_integration_staging.py --ignore=v2/tests/test_live_openai_health.py -q`) | `601 passed, 7 skipped in 1.19s` |

The 7 skipped cases are the pre-existing `test_webhook.py` Flask-only tests,
unrelated to this task. No previously passing test regressed.

### QA Check matrix (1–19 from `CURRENT_QA_TASK.md`)

| # | Check | Result | Evidence |
|---|-------|--------|----------|
| 1 | Dev stayed on V2 scope only | PASS | All file changes under `v2/`. |
| 2 | V1 production code not changed | PASS | `app.py`, `cloudflare-worker.js`, blueprints untouched (mtime 2026-05-08/09). |
| 3 | No Make.com / Cloudflare / Meta production webhook change | PASS | No such files modified. |
| 4 | No secrets written | PASS | grep clean; the only test secret string is fake and is redacted in output. |
| 5 | No live external calls in tests | PASS | grep for `requests.`/`httpx.`/`openai.`/`psycopg.connect` clean in new tests; suite runs offline in 1.2 s. |
| 6 | Admin command parsing conservative + deterministic | PASS | `parse_admin_command` only matches whitelisted heads; airline-only target falls into `ambiguous`. |
| 7 | `posts` output compact, no full captions | PASS | `test_posts_returns_summaries_not_captions` enforces line-length and absence of raw caption. |
| 8 | `mark_full` / `mark_sold_out` set override | PASS | `test_mark_full_web_code_creates_override`, `test_mark_sold_out_tour_code_real`. |
| 9 | `clear_full` / `clear_sold_out` clear override | PASS | `test_clear_full_clears_active_override` + idempotent second-call check. |
| 10 | Ambiguous targets ask for clarification | PASS | `test_ambiguous_airline_target_refused`, `test_unknown_post_id_returns_safe_message`. |
| 11 | Planning blocks tour marked full/sold_out | PASS | `test_blocks_candidate_marked_full`, `test_blocked_does_not_use_llm_text`, `TestOrchestratorBlocksFullCandidate.test_locked_tour_marked_full_returns_canned`. |
| 12 | Planning blocks via full source post | PASS | `test_blocks_candidate_from_marked_full_post`, `TestOrchestratorBlocksFullCandidate.test_post_scope_block_from_source_post_id`. |
| 13 | Planning allows candidates when no override | PASS | `test_allows_candidate_when_no_override`, `TestOrchestratorAllowsUnblockedCandidate`. |
| 14 | Response writer does not recommend blocked tours | PASS | `test_blocked_does_not_use_llm_text` asserts `"DO NOT USE"` LLM text is never produced; decision is `canned_blocked`. |
| 15 | LLM does not decide sold-out semantics | PASS | `_planning_to_compact_note` + step 2.5 short-circuit. Block reasons are `REASON_TOUR_FULL` / `REASON_DEPARTURE_FULL` / `REASON_POST_FULL` constants — deterministic. |
| 16 | LLM context compact, no full post history | PASS | `_planning_to_compact_note` drops `None`/`""`/`[]`; `test_planning_note_is_compact` asserts raw caption never appears in LLM payload. |
| 17 | No wholesale names / secrets in new bot/admin text | PASS | `_safe_text` chains `_scrub_wholesale` + `redactor.redact`. `test_no_wholesale_name_in_posts_admin_output`, `test_no_secret_in_mark_full_reason`. |
| 18 | Tests cover all required behaviours | PASS | 22 new test cases across `test_page_post_wiring.py` (18) + `test_orchestrator_planning.py` (4). |
| 19 | Broad non-live V2 suite passes (or skips justified) | PASS | 601 passed, 7 skipped (flask-only, pre-existing). |

---

## 6. Remaining Risks

- **R1.** Migration 020 is still unapplied on staging. Until that lands, real
  staging traffic will see empty `page_posts` / `tour_availability_overrides`
  tables and the planner returns a no-op `BlockDecision`. Wiring is correct;
  data is just not present yet.
- **R2.** The webhook adapter has not yet been wired to pass `source_post_id`
  / `source_type` into `Orchestrator.handle_turn(...)`. Until that is done,
  post-scope blocks only fire from in-memory tests and the
  candidate-tour-scope path. The orchestrator's optional kwargs are the
  correct seam, but production traffic gets only tour-scope coverage today.
- **R3.** The deterministic LINE adapter still has no staff allow-list
  (carry-over follow-up from `QA-2026-05-19-005`). Any LINE staff user could
  currently trigger `mark_full` / `clear_full` if the adapter forwarded to
  `handle_admin_command`. The admin command core itself is fine — the gap is
  one layer up.
- **R4.** Planning context is built on every non-silent turn. It is cheap
  (in-memory filtered `_select_all` + light dataclass construction) and
  wrapped in try/except — but it is one more per-turn cost path. After
  migration 020 is applied, run a small staging load check to confirm
  latency stays well under the existing webhook 5-second ack budget. Today
  this is a model risk only, not a measured regression — tests show
  end-to-end 1.2 s for 601 cases (≈ 2 ms/case avg).

---

## 7. Next Recommended Step

After Codex review and commit/push of the QA artifacts, in this order:

1. Apply migration `20260519_020_page_post_intelligence.sql` on staging
   once the Supabase connector is re-authenticated (and confirm RLS +
   partial unique indexes are live via `\d+`).
2. Build the Meta webhook source-attribution layer (referral / ad / post)
   so the webhook adapter passes `source_post_id` and `source_type` into
   `Orchestrator.handle_turn(...)`. Keep the change additive — orchestrator
   already accepts the kwargs.
3. Add a deterministic LINE adapter step that enforces a staff allow-list
   before forwarding admin commands to `handle_admin_command`, per the
   carry-over follow-up from `QA-2026-05-19-005`.
4. (Optional, low priority) Address L1: enrich the FEE_CHECK_REQUIRED
   handoff message with a "tour marked full by admin" signal when an
   active block exists for the locked tour, so the staff team can pivot
   to alternatives faster.

Stopping here per `docs/AI_COMMAND_CENTER.md` Handoff Rule. Awaiting Codex review.
