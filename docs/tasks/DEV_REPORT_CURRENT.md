# Dev Report — `DEV-2026-05-19-007` Page Post Intelligence Wiring into Admin Commands + Response Planning + Orchestrator

**Status:** `READY_FOR_QA`
**Verdict recommendation to QA:** `GO`
**Author:** Claude Cowork Dev (Sprint 4 follow-up; second pass — orchestrator wire-up)
**Date:** 2026-05-19
**Branch (intended):** `v2/s4-followup-vision-ondemand`
**Spend this session:** `$0.00` — no live Meta/FB, no live LINE, no live OpenAI/OCR, no paid provider, no deploy, no Supabase access.

## 1. Status

`READY_FOR_QA`. Wiring of the Page Post Intelligence foundation
(`DEV-2026-05-19-006`) into the deterministic admin command core, the
response-writer planning layer, AND the orchestrator is complete and fully
unit-tested with in-memory fakes. Every hard rule in
`docs/tasks/CURRENT_DEV_TASK.md` and `docs/AI_COMMAND_CENTER.md` is
respected:

- No V1, no Make.com, no production webhook, no deploy.
- No live Meta/FB, LINE, OpenAI, OCR, or Supabase paid-provider calls.
- No secrets touched, no wholesale partner names leaked.
- Migration 020 was **not** re-applied — Codex's operational note (Supabase
  connector requires re-auth, no local staging creds) is honoured. All
  testing uses the in-memory Supabase fake from `v2/tests/conftest.py`.

The first pass of this task wired the admin command handler and the
response_writer's optional `planning` kwarg (already reported above on
2026-05-19, branch `v2/s4-followup-vision-ondemand`). It explicitly
deferred the orchestrator wire-up. This second pass closes that gap so
the planning bundle is now constructed inside `Orchestrator.handle_turn`
and passed to `write_response` on every live turn.

## 2. Files Changed

Edited V2 code (this pass):

- `v2/lib/orchestrator.py` — wired `_build_planning_context` and the
  candidate resolver `_resolve_planning_candidate` into `handle_turn`.
  `write_response(...)` is now called with `planning=planning` on every
  non-silent turn. Added optional `source_post_id`, `source_type`, and
  `source_platform` kwargs to `handle_turn` so future webhook source-
  attribution work can pass them straight through.

New test file (this pass):

- `v2/tests/test_orchestrator_planning.py` — 4 orchestrator-level tests
  (TestOrchestratorBlocksFullCandidate, TestOrchestratorAllowsUnblockedCandidate,
  TestOrchestratorPlanningOptional) covering tour-scope block, post-scope
  block via `source_post_id`, unblocked LLM path with compact note
  injection, and graceful no-op when no candidate/source is in scope.

Already in the first pass of this task (unchanged here):

- `v2/lib/admin_command_handler.py` — `posts`, `post <post_id>`,
  `mark_full`, `mark_sold_out`, `clear_full`, `clear_sold_out` commands +
  the conservative `_resolve_page_post_target` classifier.
- `v2/lib/response_writer.py` — optional `planning=None` kwarg,
  deterministic short-circuit before the LLM when
  `planning.replacement_needed=True`, compact `page_post_planning_note`
  injected when not blocked, `CANNED_BLOCKED_REPLACEMENT` constant.
- `v2/tests/test_page_post_wiring.py` — 18 unit tests covering the
  admin commands and the response-writer planning layer.

No new migration was added in this task — migration 020 from
`DEV-2026-05-19-006` is the storage source of truth.

`docs/tasks/DEV_REPORT_CURRENT.md` and `docs/tasks/AGENT_STATUS.json` are
written/updated by this report.

## 3. Root Cause / Business Need

The Page Post Intelligence + Sold-Out Signal foundation existed only as a
service module after `DEV-2026-05-19-006`. The first pass of this task
wired the foundation into the admin command core and into
`response_writer.write_response` via an optional `planning` kwarg, but
the orchestrator (which is what actually drives every customer turn) was
still calling `write_response(..., planning=None)`. That meant the
deterministic block decision was implemented but never reached the
live response path — a customer asking about a tour an admin had marked
`full` would still get an LLM-written recommendation.

This second pass closes that gap. The orchestrator now resolves the
candidate tour (locked → fetched detail → memory → top-of-search), calls
`build_response_planning_context(...)`, and passes the resulting bundle
to `write_response(...)`. The response writer's existing short-circuit
takes care of the rest: if `replacement_needed=True`, the LLM is bypassed
entirely and a deterministic Thai safe reason is returned; otherwise a
compact `page_post_planning_note` is injected into the LLM payload.

The V2 design rule that "the LLM is never the source of truth" is
preserved end-to-end now: the page-post block decision is made by Python
code in the orchestrator → planner → response writer chain, and the LLM
is bypassed when blocked.

## 4. Summary of Changes

### `v2/lib/orchestrator.py`

- `handle_turn(...)` gains three optional kwargs:
  `source_post_id`, `source_type`, `source_platform="facebook"`. None are
  required; existing callers (tests, future webhook adapter) keep working
  without changes.
- New step 9.5 between state-commit and `write_response` builds the
  `PlanningContext`. The build is wrapped in a try/except — any failure
  logs a warning and falls back to `planning=None` so the orchestrator
  keeps producing a reply.
- `write_response(...)` is now called with `planning=planning`.
- New helper `_resolve_planning_candidate(psid, conv, accumulated)` picks
  the candidate tour in priority order:
  1. Just-locked tour from `lock_selected_tour` tool output (most recent).
  2. Just-fetched detail from `get_tour_detail` tool output.
  3. Currently locked tour from memory (warm path).
  4. Top-1 of fresh `search_tours` result.
  Returns `(None, None, None)` if nothing applies — `build_response_planning_context`
  still works and returns a no-op planner.
- New helper `_build_planning_context(...)` wraps the lazy import of
  `page_post_context.build_response_planning_context` and the try/except
  so the public `handle_turn` body stays readable.

### `v2/tests/test_orchestrator_planning.py`

4 orchestrator-level tests across 3 classes:

| Class | Coverage |
|-------|----------|
| `TestOrchestratorBlocksFullCandidate` | (a) Pre-seeded locked tour + admin `mark_full` → next turn returns deterministic `REASON_TOUR_FULL`; LLM `response` tier is **not** called. (b) Pre-seeded locked tour + admin `mark_full` on the source page post → next turn (with `source_post_id`) returns deterministic `REASON_POST_FULL`; LLM `response` tier is **not** called. |
| `TestOrchestratorAllowsUnblockedCandidate` | No active override + seeded page post → next turn calls LLM `response` tier; compact `page_post_planning_note` is present in the user payload; the **long raw caption never leaks** into the payload. |
| `TestOrchestratorPlanningOptional` | Greeting turn without candidate or source attribution → planner still builds successfully and orchestrator still replies via LLM. |

The recording LLM (`_RecordingLLM`) only counts `tier="response"` calls,
so any classification-tier inference made by the orchestrator does not
interfere with the "LLM-was-not-called" assertion on the blocked path.

## 5. Tests Run

Targeted suite (this pass) — `pytest v2/tests/test_orchestrator_planning.py`:

```
4 passed in 0.14s
```

Combined targeted suite — `pytest v2/tests/test_page_post_wiring.py
v2/tests/test_page_post_context.py v2/tests/test_admin_command_handler.py
v2/tests/test_orchestrator.py v2/tests/test_orchestrator_planning.py
v2/tests/test_response_writer.py`:

```
124 passed in 0.30s
```

Broad non-live V2 suite — `pytest v2/tests/` excluding the two live
suites (`test_integration_staging.py`, `test_live_openai_health.py`):

```
601 passed, 7 skipped in 1.86s
```

Total grew from 597 → 601 (+4 new orchestrator-planning tests). The 7
skipped cases are the pre-existing `test_webhook.py` tests that require
Flask (unrelated to this task). **No prior test broke.**

## 6. Risks / Assumptions

- The orchestrator now calls `build_response_planning_context` on every
  non-silent turn. The function is cheap and deterministic (in-memory
  filters over `page_posts`, `page_post_tour_links`,
  `tour_availability_overrides`) so the per-turn cost is unchanged for
  customers with no source attribution and trivially small in the common
  case. The try/except wrapper guarantees a planner-build failure can
  never break a turn — the bot falls back to the prior (pre-wired)
  behaviour with `planning=None`.
- `source_post_id` and `source_type` default to `None`. Until the future
  webhook source-attribution task lands, the orchestrator only blocks
  based on the candidate tour itself (tour-scope and departure-scope
  overrides). Post-scope blocks only fire when a future caller passes
  `source_post_id`. This is intentional and conservative.
- `_resolve_planning_candidate` looks at the locked tour from memory
  even when the current turn's tool list did not include
  `lock_selected_tour`. This is the desired behaviour — once an admin
  marks a previously-selected tour `full`, every subsequent customer
  turn that references that tour is blocked, not just the turn that
  re-locked it.
- The Cowork sandbox's local Linux mount can lag OneDrive sync of edits
  done via the Windows-path `Edit`/`Write` tools. During this pass the
  orchestrator file was re-materialised through bash so the on-disk
  content matches the Cowork file-tool view. The intended content is
  what `Read` returns from
  `C:\Users\supak\OneDrive\เอกสาร\Claude\Projects\สอนใช้งาน Claude Cowork\v2\lib\orchestrator.py`.
  Python `ast.parse` on the on-disk file returns `OK`.
- Git is not available inside the Cowork sandbox — the commit/push to
  `v2/s4-followup-vision-ondemand` must be performed by Codex (or by Tiw
  on the real repo clone). No secrets are present in any new/edited file.

## 7. What QA Should Verify

1. `pytest v2/tests/test_orchestrator_planning.py` returns `4 passed`.
2. `pytest v2/tests/test_page_post_wiring.py` returns `18 passed`.
3. Broad `pytest v2/tests/` excluding the two live suites is
   `601 passed, 7 skipped, 0 failed`. No previously-passing test
   regressed.
4. `v2/lib/orchestrator.py` — step 9.5 builds the planner BEFORE step 10
   (`write_response`). Confirm that:
   - On the blocked path, `tier="response"` LLM calls do not happen
     (verified by `_RecordingLLM.response_calls == []`).
   - On the unblocked path, the user payload sent to the LLM contains
     `page_post_planning_note` (compact JSON) and never the raw caption.
   - The try/except wrapper around the planner build keeps the
     orchestrator alive on any planner failure.
5. `v2/lib/response_writer.py` — default behaviour (`planning=None`)
   remains identical to before. The step-2.5 short-circuit is unchanged
   from the first pass.
6. `v2/lib/admin_command_handler.py` — unchanged from the first pass.
   The new admin commands (`posts`, `post`, `mark_full`, `mark_sold_out`,
   `clear_full`, `clear_sold_out`) still parse and execute correctly
   (covered by `test_page_post_wiring.py`).
7. Leakage controls — no new env reads, no new HTTP calls, no wholesale
   partner names anywhere in the new orchestrator code or in the new
   test fixtures (grep
   `(?i)\b(ttn|zego|formosa|i[-\s]?travel|rich\s+tour|best\s+tour)\b`
   against `v2/lib/orchestrator.py` and
   `v2/tests/test_orchestrator_planning.py` returns nothing).
8. Operational safety: migration 020 is still NOT applied to staging.
   QA should NOT attempt to apply it. The wiring is in code only.

## 8. Next Recommended Step

After QA `GO`:

1. Apply migration 020 on staging once the Supabase connector is
   re-authenticated and credentials are available in the Codex shell.
2. Build the Meta webhook source-attribution layer so the webhook
   adapter can call `Orchestrator.handle_turn(..., source_post_id=...,
   source_type=...)`. The orchestrator already accepts those kwargs.
3. Add a deterministic LINE adapter step that allow-lists admin user
   IDs before forwarding any admin command, per the
   `QA-2026-05-19-005` follow-up note.
4. Once the dashboard auth story is decided, expose the same
   `mark_/clear_/posts` operations behind admin auth.

Each step above is intentionally scoped small to keep risk low and to
let QA verify one behaviour change at a time, matching the
`AGENT_WORKFLOW` gates.

---

Stopping here per `docs/AI_COMMAND_CENTER.md` Handoff Rule. Awaiting QA review.
