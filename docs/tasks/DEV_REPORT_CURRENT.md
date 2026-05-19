# Dev Report — `DEV-2026-05-19-006` Page Post Intelligence + Sold-Out Signal Foundation

**Status:** `READY_FOR_QA`
**Verdict recommendation to QA:** `GO`
**Author:** Claude Cowork Dev
**Date:** 2026-05-19
**Branch (intended):** `v2/s4-followup-vision-ondemand`
**Spend this session:** `$0.00` — no live Meta/FB, no live LINE, no live OpenAI/OCR, no paid provider, no deploy.

## 1. Status

`READY_FOR_QA`. The foundation is implementation-complete, fully unit-tested in
the sandbox, and respects every hard rule in `docs/tasks/CURRENT_DEV_TASK.md`
and `docs/AI_COMMAND_CENTER.md`. No V1 code, Make.com scenario, production
webhook, or paid-provider code path was modified or invoked.

## 2. Files Changed

New files:

- `v2/supabase/migrations/20260519_020_page_post_intelligence.sql`
- `v2/lib/page_post_context.py`
- `v2/tests/test_page_post_context.py`

Edited docs (no V2/V1 code-behaviour change outside this task):

- `docs/V2_PAGE_POST_INTELLIGENCE_PLAN.md` — status flipped to
  `FOUNDATION_IMPLEMENTED`; added the implementation contract and follow-ups.
- `docs/V2_DATA_MODEL.md` — added section 7.b describing the three new tables.

This report (`docs/tasks/DEV_REPORT_CURRENT.md`) and
`docs/tasks/AGENT_STATUS.json` are also written/updated.

## 3. Root Cause / Business Need

Most TourFireMai daily sales traffic arrives from Facebook page posts and ads.
Today the V2 bot starts every conversation from zero — it does not know that
the admin just posted a fire-sale tour, that the tour the customer is asking
about is full, or that the customer arrived from an organic chat versus an ad.
The business invariant that triggered this task is:

> If a customer comes from a recent page post / ad / organic source that points
> to a tour marked full / sold-out by admin, the bot must not recommend that
> sold-out option. It should acknowledge the post context and offer the closest
> available alternatives.

Because the V2 design rule is that the LLM is never the source of truth, the
sold-out decision must be deterministic Python + DB.

## 4. Summary of Changes

### Migration `20260519_020_page_post_intelligence.sql`

Three new tables (additive; no existing table modified):

| Table | Purpose |
|-------|---------|
| `page_posts` | Recent page posts keyed uniquely by `(platform, post_id)`. Includes `text_hash` (sha256 of caption), explicit `active_until` override of the default 3-day window, `source_type ∈ {page_post, ad, organic, unknown}`, and status. |
| `page_post_tour_links` | N:M between page posts and tours. `web_code`, `tour_code_real`, and `tour_id` are nullable (at least one required); partial unique indexes give per-(post, code) idempotency. `tour_id` is intentionally NOT FK to `tours_canonical` so admin can pre-mark a brand-new fire-sale code before the scraper has produced its canonical row. |
| `tour_availability_overrides` | Admin-marked `sold_out` / `full` / `unknown` / `available` overrides. `scope ∈ {tour, departure, post}`; partial unique indexes ensure only one **active** row per (target, scope, departure_date). Audit-preserving: `cleared_at` (with `cleared_by`) is flipped instead of deleting. |

All three tables: `service_role` full access, `anon` denied, RLS enabled —
identical pattern to migrations 001-019.

### Module `v2/lib/page_post_context.py`

Pure deterministic service layer. No env reads. No live network. No LLM. No
secrets. No wholesale partner names.

Public API:

- `upsert_page_post(...)` — idempotent on `(platform, post_id)`, recomputes
  `text_hash` on re-ingest, validates platform and source_type.
- `list_recent_page_posts(days=3, ...)` — returns active posts within the
  relevance window (`active_until` if set, else `posted_at + days`), newest
  first, with linked codes and `is_post_blocked` flag.
- `extract_tour_references(text)` — extracts `web_code`, `tour_code_real`, and
  URLs from free text. Reuses `v2.lib.tour_codes.normalize_*` so airline tokens
  (e.g. `HU`) are never mis-classified as tour codes.
- `link_page_post_to_tour(...)` and `link_page_post_from_text(...)` —
  idempotent N:M linkage with code-shape validation.
- `mark_availability_override(...)` — admin marks tour / departure / post; if
  an active override already exists for the same (target, scope,
  departure_date), it is auto-cleared and a new row inserted (preserves audit).
- `clear_availability_override(...)` — by `override_id` or by (scope, target).
- `is_candidate_blocked(...)` — deterministic block decision with precedence
  `departure > post > tour`; returns canned Thai reason text. Status `unknown`
  is intentionally **not** blocking.
- `get_source_context(...)` — compact, LLM-safe summary (`page_post`, `ad`,
  `organic`, `unknown`); title is single-line, length-capped (80 chars max),
  wholesale-scrubbed, redactor-cleaned.
- `build_response_planning_context(...)` — single planner entrypoint that
  returns the source summary, block decision, `replacement_needed` flag, and a
  safe Thai reason text capped at 160 chars.

Constants exposed for QA/test reuse:

```
DEFAULT_RECENT_WINDOW_DAYS = 3
MAX_RECENT_WINDOW_DAYS     = 30
CONTEXT_TITLE_MAX_CHARS    = 80
CONTEXT_REASON_MAX_CHARS   = 160
REASON_TOUR_FULL, REASON_DEPARTURE_FULL, REASON_POST_FULL,
REASON_AVAILABLE_FROM_POST   # fixed Thai templates
```

Wholesale partner names are filtered through the same
`response_writer._WHOLESALE_BLACKLIST` and replaced with the redaction token
`***WHOLESALE-REDACTED***`. Secret-pattern leaks (API keys, JWTs, PSIDs,
emails, phones) are routed through `v2.lib.redactor.redact`.

### Tests `v2/tests/test_page_post_context.py`

41 unit tests across 7 classes covering all 12 required behaviours in the task
brief. Every test uses the in-memory Supabase fake from
`v2/tests/conftest.py` — no real DB, no network, no live provider call.

## 5. Tests Run

Targeted suite — `pytest v2/tests/test_page_post_context.py`:

```
41 passed in 0.12s
```

Broad non-live V2 suite — `pytest v2/tests/` excluding the three live suites
(`test_integration_staging.py`, `test_live_openai_health.py`,
`test_phase2_live_followup.py`):

```
563 passed, 7 skipped in 2.03s
```

Skipped cases are pre-existing `test_webhook.py` tests that require Flask
(unrelated to this task). **No prior test broke.**

## 6. Risks / Assumptions

- The in-memory Supabase fake (`v2/tests/conftest.py::InMemorySupabase`) does
  not enforce partial unique indexes; the migration enforces them on real
  Postgres. The new module is structured to avoid duplicate inserts in code,
  so the partial uniques are belt-and-braces. QA should still apply the
  migration on staging Postgres to confirm.
- `tour_availability_overrides.tour_id` is intentionally NOT FK to
  `tours_canonical`. This is documented inside the migration. Confirm
  acceptable for the dashboard contract.
- Source-type inference is conservative: if no matching `page_posts` row
  exists for the `post_id`, the source is reported as `unknown`. The future
  webhook source-attribution task must upsert the row before this lookup runs.
- Status `unknown` is treated as non-blocking on purpose so admin can clear a
  block without forcing a hard `available`. If business prefers `unknown` to
  be soft-blocking, change `_BLOCKING_STATUSES` in `page_post_context.py`.
- Git is **not available inside the Cowork sandbox** (the workspace is the
  selected folder mounted from the user's filesystem, not a git working tree).
  The commit and push to `v2/s4-followup-vision-ondemand` therefore must be
  performed by Codex (or by Tiw on the real repo clone). All file paths above
  are the source of truth for that commit. No secrets exist in any new file.

## 7. What QA Should Verify

1. Migration `20260519_020_page_post_intelligence.sql` applies cleanly to
   staging Postgres after migration 019, including partial unique indexes.
2. Re-running the migration is a no-op
   (`CREATE TABLE IF NOT EXISTS` / `CREATE UNIQUE INDEX IF NOT EXISTS`).
3. `pytest v2/tests/test_page_post_context.py` returns `41 passed`.
4. Broad `pytest v2/tests/` (excluding the three live suites) still passes;
   no prior test regressed.
5. `v2/lib/page_post_context.py`:
   - Imports only `v2.lib.redactor`, `v2.lib.response_writer` (for the
     wholesale blacklist regex), and `v2.lib.tour_codes`. No env reads.
   - All public functions accept `supabase` + optional `now`. No module-level
     globals beyond constants.
6. Leakage controls:
   - Compact title/reason strings never contain raw wholesale partner names
     (test class `TestLeakageSafety`).
   - Title length cap is honoured for very long captions
     (`TestCompactContext.test_title_does_not_include_excessive_text`).
   - Bot-facing safe reason is a fixed Thai template, never derived from the
     admin's free-text reason.
7. Block precedence: departure-scope override wins over tour-scope override on
   the same code (`TestBlocking.test_departure_scope_takes_precedence`).
8. Expired override (`expires_at <= now`) is treated as inactive
   (`TestBlocking.test_not_blocked_when_override_expired`).
9. Orchestrator / response_writer / admin_ops / admin_command_handler are
   unchanged — no prior public function was edited.
10. No Meta Graph API code path is introduced. Grep for `graph.facebook.com`,
    `requests.get`, `httpx`, or any live HTTP call returns nothing in the new
    module.
11. No `OPENAI_*` / `LINE_*` / `FB_*` / `SUPABASE_*` env reads in the new
    code.
12. No wholesale-partner brand string appears anywhere in the new files
    (grep `(?i)\b(ttn|zego|formosa|i[-\s]?travel|rich\s+tour|best\s+tour)\b`).

## 8. Next Recommended Step

After QA `GO`:

1. Wire `build_response_planning_context(...)` into
   `v2/lib/response_writer.py` so the LLM is gated by the deterministic block
   decision and is fed only the compact source-context summary.
2. Add deterministic admin LINE commands `posts`, `mark_full <code>`,
   `clear_full <code>` that wrap the new module — mirror the pattern in
   `v2/lib/admin_command_handler.py`.
3. Once the dashboard auth story is decided, expose a read/write API for the
   three new tables behind admin auth.
4. Wire Meta webhook source-attribution (referral / ad / post_id) so
   `source_post_id` is filled before `get_source_context` runs.

Each step above is intentionally scoped small to keep risk low and to let QA
verify one behaviour change at a time, matching the AGENT_WORKFLOW gates.

---

Stopping here per `docs/AI_COMMAND_CENTER.md` Handoff Rule. Awaiting QA review.
