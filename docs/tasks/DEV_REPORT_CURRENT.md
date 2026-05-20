# DEV-2026-05-20-014 — Dev Report

Status: `READY_FOR_QA`
Role: Claude Dev
Branch: `v2/s4-followup-vision-ondemand`
Base commit: `4ef8114`
Generated: 2026-05-20

---

## 1. Summary

Wired the QA-cleared detail-page departure parser (DEV-012) and selected
departure matcher (DEV-013) into the V2 orchestrator and response writer
so that after a customer selects a tour, the bot:

- keeps the locked selected tour in scope across follow-up turns;
- enriches the `/tour/<web_code>` detail page deterministically, on
  demand, only when the customer's message is a selected-tour follow-up;
- matches the customer's date phrase against the parsed departure rows
  via the DEV-013 matcher (no LLM, no guesses);
- passes a compact, LLM-safe planning bundle into the response writer
  with the matched row data (only when confidence is high), available
  departures, and a deterministic Thai `safe_planning_note` instructing
  the LLM not to confirm seat availability or final price;
- never mixes `web_code`, `tour_code_real`, or `airline`;
- preserves `None` for missing prices ("-" cells stay `None`, never `0`);
- keeps the page-post / sold-out / full deterministic block path
  (DEV-2026-05-19-007) winning before the LLM is called.

The change is additive only. V1 production is untouched. Make.com remains
deactivated. No production webhook settings, secrets, deploys, or live
paid-provider calls were performed.

## 2. Files changed

```
v2/lib/orchestrator.py                       (modified - wiring + helpers)
v2/lib/response_writer.py                    (modified - new selected_departure kwarg)
v2/lib/selected_departure_planning.py        (new - compact, LLM-safe planning bundle)
v2/tests/test_selected_departure_planning.py (new - 16 focused integration tests)
docs/tasks/DEV_REPORT_CURRENT.md             (modified - this report)
docs/tasks/AGENT_STATUS.json                 (modified - READY_FOR_QA)
```

No migrations were authored, applied, or scheduled. No prod-facing route
or webhook handler was touched.

## 3. Implementation details

### 3.1 New module: v2/lib/selected_departure_planning.py

A pure, deterministic module that turns parsed `DeparturePriceRow`s plus a
customer's date phrase into a compact `SelectedDeparturePlanning`
dataclass.

- No LLM, no network, no DB, no OCR, no paid providers.
- Public API: `build_selected_departure_planning(...)`,
  `SelectedDeparturePlanning`, `compact_departure_dict`,
  `row_dict_to_departure_price_row`.
- Match-status constants: `matched_high`, `matched_medium`,
  `matched_low`, `ambiguous`, `no_match`, `unparseable`, `no_phrase`,
  `no_rows`.
- `safe_planning_note` carries a deterministic Thai instruction for the
  LLM. The note always reminds the LLM that it must not confirm seat
  availability or final price.
- `to_compact_dict()` drops empty values to keep the LLM payload tight
  but always retains `match_status` and `ask_confirmation` because those
  False / no-match values are meaningful.

### 3.2 Orchestrator wiring: v2/lib/orchestrator.py

- `Orchestrator.__init__` gains two optional kwargs:
  - `http_client: Optional[Any] = None` - when None the orchestrator
    never attempts a live HTTP fetch (graceful no-op).
  - `detail_fetch_ttl_s: int = 300` - Redis guard window so the same
    `/tour/<web_code>` is not re-fetched on every message.
- New `_DepartureCandidate` dataclass plus
  `_resolve_selected_departure_candidate(...)` follow the documented
  priority order:
    1. `accumulated["lock_selected_tour"]` (just-selected in turn)
    2. memory lock from `memory.get_selected_tour(psid)`
    3. explicit `web_code` / `tour_code_real` in customer text
       (`intent.selected_code`) via `tours_canonical` lookup
    4. in-turn `accumulated["get_tour_detail"]`
    5. recent offer snapshot top-N + `intent.selected_index`
  `tour_id` / `airline` are backfilled from `tours_canonical` when
  missing - never collapsed across the three code fields.
- `_should_trigger_detail_enrichment(...)` blocks enrichment for
  greeting / payment / attachment / decline / off-topic intents. Always
  triggers for selected-tour follow-up intents (`select_tour`,
  `select_departure`, `ask_fee`, `ask_tour_detail`, `confirm_booking`,
  `ask_pax`, `ask_period`), for parseable date phrases, and for
  any non-trivial text when a memory-locked tour is in scope.
- `_get_or_fetch_departure_rows(...)` is DB-first:
    - If `tour_departures` has rows for the `web_code`, convert via
      `row_dict_to_departure_price_row` and return.
    - Otherwise consult the Redis guard
      (`detail_fetched:{web_code}` with TTL `detail_fetch_ttl_s`) before
      calling `enrich_tour_detail(...)`.
    - With `http_client=None`, returns `[]` (graceful no-op).
- `_build_selected_departure_planning(...)` ties it together. Never
  raises - returns `None` on any failure so the orchestrator can still
  produce a reply via the other paths.
- `handle_turn(...)` now builds both the existing page-post planning
  bundle and the new selected-departure planning bundle, then passes
  `selected_departure=` into `write_response(...)`. Silent states still
  short-circuit before any planning work.

### 3.3 Response writer: v2/lib/response_writer.py

- Added `selected_departure=None` kwarg. When non-None, its
  `to_compact_dict()` is `_strip_wholesale`-scrubbed and merged into
  `clean_tools` under the key `selected_departure_planning` BEFORE the
  LLM payload is built. The LLM therefore receives the planning bundle
  but never the raw rows or wholesale fields.
- The page-post / sold-out canned block (`replacement_needed`) still
  wins ahead of the LLM call - Sprint 5 Package H does not change that
  ordering.
- `FEE_CHECK_REQUIRED` canned-fee branch is unchanged: it ignores
  `selected_departure` and uses `fee_answer_policy` as today.

### 3.4 New tests: v2/tests/test_selected_departure_planning.py

16 tests covering all 10 required cases from `CURRENT_DEV_TASK.md`:

1. Generic greeting / broad country ask does NOT fetch the detail page.
2. After `lock_selected_tour`, the orchestrator enriches detail once and
   the second follow-up turn re-uses the DB rows (no extra HTTP call).
3. A date phrase that exactly matches a row passes the compact row data
   to the LLM payload with `match_status == matched_high`.
4. A fee follow-up after a selected tour does NOT clear the selected
   tour from memory.
5. An ambiguous date phrase produces a confirmation-style planning
   bundle (`ask_confirmation=True`, two `ambiguous_candidates`, no
   `matched_departure`).
6. A date phrase with no matching row surfaces `available_departures`
   for the LLM to ask the customer to pick from.
7. `web_code`, `tour_code_real`, and `airline` stay mutually distinct
   on both the top-level planning dict and the matched row.
8. "-" cells stay None on the matched row (never coerced to `0`).
9. An admin `full` override on the tour scope still drives the canned
   blocked reply BEFORE the LLM is called (regression for
   DEV-2026-05-19-007).
10. The planning module never imports `requests`, `openai`, `anthropic`,
    `boto3`, or `supabase` at module level; and the orchestrator never
    attempts a live HTTP call when `http_client=None`.

Bonus tests:
- Candidate-resolution priority covers `just_locked > memory_locked`,
  `intent_code > in_turn_detail`, and the no-candidate base case.
- `row_dict_to_departure_price_row` round-trips a persisted
  `tour_departures` row back to a `DeparturePriceRow` without losing
  codes or coercing `-` to `0`.

## 4. Test results

All runs offline. No live network, no live LLM, no live Supabase, no
live LINE/Meta/OCR/paid-provider calls.

Targeted suite (DEV-014 + adjacency):

```
pytest v2/tests/test_orchestrator_planning.py \
       v2/tests/test_detail_enrichment.py \
       v2/tests/test_selected_departure_match.py \
       v2/tests/test_selected_departure_planning.py \
       --basetemp=.pytest_tmp -p no:cacheprovider -q
```

Result: `71 passed in 0.30s`.

Adjacent (orchestrator + response writer):

```
pytest v2/tests/test_orchestrator.py v2/tests/test_response_writer.py \
       v2/tests/test_orchestrator_planning.py \
       v2/tests/test_selected_departure_planning.py \
       --basetemp=.pytest_tmp -p no:cacheprovider -v
```

Result: `70 passed in 0.35s` (all 16 new planning tests + 54 existing
orchestrator/response-writer tests - no regressions).

Broad non-live V2 suite:

```
pytest v2/tests \
       --ignore=v2/tests/test_integration_staging.py \
       --ignore=v2/tests/test_live_openai_health.py \
       --ignore=v2/tests/test_phase2_live_followup.py \
       --basetemp=.pytest_tmp -p no:cacheprovider -q
```

Result: `783 passed, 40 skipped, 0 failed in 3.70s`.

Skips are exclusively flask-only tests where the optional `flask`
package is not installed in the sandbox; they skip cleanly the same
way they did in DEV-2026-05-20-013.

## 5. Safety / scope guard verification

- V1: untouched. No file under `legacy/` / `v1/` was modified.
- Make.com: untouched. Scenario 4967547 stays deactivated.
- Deploy: none.
- Production Meta webhook settings: untouched.
- Secrets: none read, printed, rotated, or persisted.
- Live paid providers: zero calls. No OpenAI / Anthropic / OCR /
  Document AI / Meta / LINE traffic.
- Supabase migrations: none authored or applied.
- Customer-wide traffic: not enabled. Admin-only test posture preserved.
- Wholesale partner names: never appear in any returned string or test
  fixture. `_strip_wholesale` runs over the new
  `selected_departure_planning` payload before it reaches the LLM
  (defense in depth alongside the planning module's own filtering).
- `web_code` / `tour_code_real` / `airline`: kept strictly separate
  everywhere. Asserted by tests 7 and the priority-order tests.
- Past dates: rejected by the matcher (carried over from DEV-013, still
  asserted via `TestMatchDepartureNoMatch.test_past_date_rejected`).
- "-" / missing values: stay None. Asserted by test 8 and the
  round-trip test.
- Sold-out / full overrides: still win before the LLM. Asserted by test
  9. The new planning bundle never tries to bypass the deterministic
  blocker.

## 6. Known notes / risks

- The Redis guard `detail_fetched:{web_code}` has a default 300-second
  TTL. The DB persistence from `enrich_tour_detail` is the primary
  cache; the guard prevents redundant HTTP fetches when DB is empty
  (e.g., a tour with no published departures yet). Operators can tune
  via `Orchestrator(detail_fetch_ttl_s=...)` if needed.
- Repeat customers across very long conversations will see DB rows
  served indefinitely (DB-first). A separate refresher schedule should
  re-trigger detail enrichment periodically so prices stay current -
  that scheduler is out of scope for DEV-014.
- The orchestrator constructs the planning bundle only for non-silent
  states. Silent states (human_paused / closed) still skip both planning
  bundles, which matches the existing page-post planning behavior.
- The LLM is now exposed to a slightly larger `tool_results` payload
  when both `page_post_planning_note` and `selected_departure_planning`
  are present. Payload growth in dev runs was ~600-1200 bytes - well
  under any tier cap.
- The DEV-013 follow-up notes (migration 021 housekeeping, scheduled
  refresher) remain open. None are required for this wiring and none
  are touched here.

## 7. Exact QA focus areas

QA should verify in this order:

1. Code review of:
   - `v2/lib/selected_departure_planning.py` (no LLM / network / DB
     imports, deterministic match-status mapping, compact dict
     correctness, planning notes never quote a price/seat).
   - `v2/lib/orchestrator.py` candidate resolver (priority order),
     trigger gate (greeting/attachment/decline must be silent on
     enrichment), DB-first + Redis guard, no raising in the planning
     path, ordering of `selected_departure` after `planning` so the
     sold-out canned block still wins.
   - `v2/lib/response_writer.py` injection of
     `selected_departure_planning` through `_strip_wholesale` before the
     LLM call.
2. Run the targeted suite:
   - `pytest v2/tests/test_orchestrator_planning.py v2/tests/test_detail_enrichment.py v2/tests/test_selected_departure_match.py v2/tests/test_selected_departure_planning.py --basetemp=.pytest_tmp -p no:cacheprovider -q`
3. Run the broad non-live V2 suite:
   - `pytest v2/tests --ignore=v2/tests/test_integration_staging.py --ignore=v2/tests/test_live_openai_health.py --ignore=v2/tests/test_phase2_live_followup.py --basetemp=.pytest_tmp -p no:cacheprovider -q`
4. Scope discipline check:
   - No V1 / Make.com / migration / deploy / production webhook / secret
     changes.
   - No live paid-provider calls anywhere in the new test runs.
5. Bonus: trace a synthetic "greeting -> select_tour -> date follow-up
   -> fee follow-up" sequence to confirm:
   - HTTP fake records exactly one call.
   - Selected tour memory persists across all four turns.
   - LLM payload contains `selected_departure_planning` only on the
     date and fee follow-ups (not on the greeting).

## 8. Recommendation

`GO` - the integration is complete, all 71 targeted tests and 783 broad
non-live tests pass, hard rules are honored, and the page-post /
sold-out deterministic block still wins ahead of the LLM. Owner (Tiw)
approval remains required before any production webhook / live LLM /
customer-wide traffic step.
