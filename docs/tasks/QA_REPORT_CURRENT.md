# QA Report — `QA-2026-05-19-006`

**Verdict:** `GO`
**Author:** Claude Cowork QA (read-only review)
**Date:** 2026-05-19
**Controller:** Codex
**Paired Dev task:** `DEV-2026-05-19-006` (V2 Page Post Intelligence + Sold-Out Signal foundation)
**Branch named in control files:** `v2/s4-followup-vision-ondemand`

---

## 1. Verdict

**`GO`.** The Dev output delivers a clean, well-isolated foundation for the V2 Page Post Intelligence + Sold-Out Signal layer. The migration is additive and idempotent; the new deterministic service module (`v2/lib/page_post_context.py`) holds the entire sold-out / source-attribution decision logic in pure Python with no env reads and no live network calls; and the 41-test suite in `v2/tests/test_page_post_context.py` covers all twelve required behaviors. The broad non-live V2 suite continues to pass with no regressions (563 passed / 7 skipped flask-only / 0 failed). No prior V2 module was modified, V1 / Make.com / Cloudflare / production webhook are untouched, no secrets appear in any new file, and no wholesale partner brand name leaks into any returned string.

The implementation matches both `docs/AI_COMMAND_CENTER.md` Hard Safety Rules and the explicit boundaries set in `docs/tasks/CURRENT_DEV_TASK.md` and `docs/V2_PAGE_POST_INTELLIGENCE_PLAN.md`. The work is ready for Codex to commit and push on the real repo clone, and for the next downstream task (response-writer wiring or LINE admin command extension) to consume the new API.

---

## 2. Scope Reviewed

Source-of-truth files read:

1. `docs/AI_COMMAND_CENTER.md`
2. `docs/V2_PAGE_POST_INTELLIGENCE_PLAN.md`
3. `docs/tasks/CURRENT_DEV_TASK.md`
4. `docs/tasks/CURRENT_QA_TASK.md`
5. `docs/tasks/TASK_LOG.md`
6. `docs/tasks/DEV_REPORT_CURRENT.md`
7. `docs/tasks/AGENT_STATUS.json`

Dev-changed V2 files read in full:

- `v2/supabase/migrations/20260519_020_page_post_intelligence.sql` (205 lines)
- `v2/lib/page_post_context.py` (1220 lines)
- `v2/tests/test_page_post_context.py` (573 lines)
- `docs/V2_PAGE_POST_INTELLIGENCE_PLAN.md` (status flipped to `FOUNDATION_IMPLEMENTED`, contract section added)
- `docs/V2_DATA_MODEL.md` (section 7.b describing the three new tables)

Supporting files read to verify integration:

- `v2/lib/response_writer.py` (lines 30–90, to confirm `_WHOLESALE_BLACKLIST` is the import target the new module reuses)
- `v2/tests/conftest.py` (lines 1–220, to confirm the in-memory Supabase fake duck-types the methods the new module calls: `select_one`, `select_all`, `insert`, `update`)

---

## 3. Evidence Checked

### 3.a Scope discipline — `PASS`

- No edits to `app.py`, no edits to any `make_blueprint*.json`, no edits to `cloudflare-worker.js`, no edits to V1 schema files (`supabase_schema.sql`, `dashboard_*.sql`, `tourcode_migration.sql`, etc.). Directory listing confirms all V1 artifacts retain May timestamps prior to this task.
- No edits to any prior V2 module — grep for `page_post_context` across `v2/lib/` returns only the new module itself; orchestrator, response_writer, admin_ops, admin_command_handler, memory, idempotency, state_machine are untouched.
- No production webhook code path is introduced. Grep for `graph.facebook.com`, `requests.get`, `httpx`, `OPENAI_`, `LINE_CHANNEL`, `FB_`, `SUPABASE_URL`, `SUPABASE_KEY`, `os.environ`, `os.getenv`, `getenv` across the three new files returns zero matches.
- No `meta_message_id` / customer PSID is logged unredacted — the one `logger.info` call in the module routes `marked_by` through `redactor.redact(...)`.

### 3.b Migration shape — `PASS`

- File header documents purpose, business need, and additive-only contract.
- All three new tables (`page_posts`, `page_post_tour_links`, `tour_availability_overrides`) are created with `CREATE TABLE IF NOT EXISTS`. Re-applying the migration is a no-op.
- All unique indexes use `CREATE UNIQUE INDEX IF NOT EXISTS`; all helper indexes use `CREATE INDEX IF NOT EXISTS`. Idempotent.
- `ALTER TABLE … ENABLE ROW LEVEL SECURITY` followed by `DROP POLICY IF EXISTS … CREATE POLICY …` — idempotent and matches the pattern in migrations 001–019.
- `service_role` is granted full access; `anon` is denied (`USING (false)`). Consistent with prior migrations.
- CHECK constraints prevent obviously-bad rows:
  - `chk_pp_post_id_nonblank` / `chk_pp_page_id_nonblank` reject empty IDs.
  - `chk_pptl_at_least_one_code` requires at least one of `web_code` / `tour_code_real` / `tour_id`.
  - `chk_pptl_codes_differ` and `chk_tao_codes_differ` prevent `web_code == tour_code_real`.
  - `chk_tao_target_set` enforces scope-dependent target presence.
  - `chk_tao_departure_when_scope` enforces `departure_date` when scope='departure'.
- Partial unique indexes on `tour_availability_overrides` use `COALESCE(departure_date, DATE '0001-01-01')` so a NULL departure_date is treated as a fixed sentinel — preventing duplicate active overrides on the same target.
- `page_post_tour_links.tour_id` is intentionally NOT FK to `tours_canonical`. Comment in the migration explains why (admins can pre-mark pre-canonical fire-sale codes). Acceptable for the dashboard contract.
- No existing table is altered. Migration is purely additive.

### 3.c Module behaviors — `PASS`

Verified by reading `v2/lib/page_post_context.py` end-to-end against the 12 required behaviors:

1. **Upsert idempotency by `(platform, post_id)`** — `upsert_page_post` first calls `select_one({"platform", "post_id"})`; if found, it issues `update` against that row's id; otherwise it `insert`s a new row with a freshly generated UUID and `ingested_at = now`. `text_hash` is recomputed on re-ingest (sha256 of caption). Validated by `TestUpsertIdempotency.test_upsert_inserts_then_updates_in_place` which also asserts only one row remains in storage.
2. **3-day recent-post filter (default + override)** — `_is_recent` returns `True` only if `status == "active"` AND (`active_until >= now` if set, else `posted_at >= now - days`). `list_recent_page_posts` validates `days` is in `(0, MAX_RECENT_WINDOW_DAYS]`, sorts results newest-first, and limits to `limit`. Validated by `TestRecentWindow` (5 tests).
3. **Web-code extraction from URL + plain text** — `_WEB_CODE_TOKEN_RE = r"\b([a-z]{2,3}\d{5,7})\b"` with case-insensitive flag, then routed through `normalize_web_code`. Verified by `test_extracts_web_code_from_url` and `test_extracts_web_code_from_plain_text`.
4. **Real tour-code extraction; airline rejection** — Tokens matching `r"\b([A-Z][A-Z0-9\-]{2,})\b"` are routed through `normalize_tour_code_real`, which is the canonical normalizer in `v2/lib/tour_codes`. Verified by `test_extracts_real_tour_code` and `test_airline_alone_not_extracted_as_tour_code` (HU airline rejected).
5. **Idempotent N:M linking** — `link_page_post_to_tour` validates code shape, then scans existing links for the same `page_post_id` and matches on `web_code` / `tour_code_real` / `tour_id`. Existing match → in-place update; otherwise insert. Multiple distinct codes against the same post are stored as separate rows. Validated by 4 tests in `TestLinking`.
6. **Mark sold_out / full at tour, departure, post scope** — `mark_availability_override` validates scope-specific target requirements, finds any existing active override on the same `(scope, target, departure_date)` tuple, clears it (`cleared_at`, `cleared_by` set) to preserve audit, then inserts a new row. Validated by `TestMarkAndClearOverride.test_mark_replaces_existing_active_override`.
7. **Clear override by id or by target** — `clear_availability_override` supports both modes: `override_id` clears one specific row; `scope + target` clears whichever active row matches. Returns count cleared. Validated by `test_clear_by_target_clears_active_row` and `test_clear_by_id_clears_specific_row`.
8. **Block when active override exists** — `is_candidate_blocked` has precedence `departure > post > tour`. Verified by `test_blocked_when_tour_marked_sold_out`, `test_post_scope_blocks_candidate_from_post`, `test_departure_scope_takes_precedence`.
9. **Allow when no override or override expired** — `_override_active` returns False if `cleared_at` is set OR `expires_at <= now`. Verified by `test_not_blocked_when_no_override`, `test_not_blocked_after_clear`, `test_not_blocked_when_override_expired`, `test_unknown_status_is_not_blocking`.
10. **Source context: page_post / ad / organic / unknown** — `get_source_context` infers `page_post` if a `(platform, post_id)` row exists, otherwise `unknown`. Explicit `source_type` parameter overrides inference. Validated by `TestSourceContext` (4 tests).
11. **Compact context — no excessive post text** — `_shorten_title` collapses whitespace, applies `_safe_text` (which routes through `_scrub_wholesale` then `redactor.redact`), and caps at `CONTEXT_TITLE_MAX_CHARS = 80`. Reasons cap at `CONTEXT_REASON_MAX_CHARS = 160`. Validated by `test_title_does_not_include_excessive_text` and `test_planning_safe_reason_capped`.
12. **No secrets, no wholesale partner names** — `_WHOLESALE_BLACKLIST` is imported from `v2.lib.response_writer` (single source of truth). `_scrub_wholesale` replaces the entire string with `***WHOLESALE-REDACTED***` if any pattern matches. `_safe_text` chains `_scrub_wholesale` → `redactor.redact`. Validated by `TestLeakageSafety` (3 tests).

### 3.d Tests — `PASS`

Targeted suite — `pytest v2/tests/test_page_post_context.py`:

```
41 passed in 0.22s
```

Broad non-live V2 suite — `pytest v2/tests/ --ignore=test_integration_staging.py --ignore=test_live_openai_health.py --ignore=test_phase2_live_followup.py`:

```
563 passed, 7 skipped in 1.93s
```

Skips are pre-existing `test_webhook.py` tests gated on Flask install. They are unrelated to this task. No prior V2 test broke.

QA verified the same totals Dev claimed in the report (41 targeted / 563 broad).

### 3.e Doc updates — `PASS`

- `docs/V2_PAGE_POST_INTELLIGENCE_PLAN.md` — status flipped to `FOUNDATION_IMPLEMENTED` and a "Foundation Layer Contract" section was added pointing to the three new tables, the public functions, and the hard rules enforced by tests.
- `docs/V2_DATA_MODEL.md` — section 7.b documents the three new tables (`page_posts`, `page_post_tour_links`, `tour_availability_overrides`) with CREATE-TABLE-equivalent shapes.

---

## 4. Findings by Severity

### Critical — none

### High — none

### Medium — none

### Low (non-blocking observations)

L1. **`_URL_RE` is intentionally broad.** The regex `https?://(?:www\.)?[^\s)]*` captures any URL, not just `tourfiremai.com`. Tests only assert that `tourfiremai` URLs are recovered, but the regex would also pick up Facebook permalinks, etc. This is acceptable because `urls` is captured for inspection only; web codes / real codes are extracted via separate regexes. No action needed.

L2. **Source-type inference treats a single match generously.** `get_source_context` infers `page_post` if ANY row matches `(platform, post_id)` — including stale posts older than `days`. The `is_recent` flag tells callers whether the row falls inside the recency window, but downstream callers must read `is_recent` rather than `source_type` alone if they care about freshness. The Dev report already documents this. Worth surfacing in the response-writer wiring task.

L3. **`unknown` status is intentionally non-blocking.** Per the Dev report and `_BLOCKING_STATUSES = {"sold_out", "full"}`, a status of `unknown` does not block a candidate. Test `test_unknown_status_is_not_blocking` covers it. If the product later decides `unknown` should be soft-blocking, change `_BLOCKING_STATUSES`. Documented in Dev report assumptions.

L4. **`_validate_source_type` is called twice in `get_source_context`** — once for `inferred` (line 1086) and once for `final_source` (line 1109). Harmless defensive redundancy; no action needed.

L5. **In-memory Supabase fake does not enforce partial unique indexes.** The module avoids duplicate inserts in code; the partial uniques in the migration are belt-and-braces. The Dev report flags this explicitly. QA should ensure the migration is applied on staging Postgres in the next deploy step to confirm the database side rejects rogue inserts. Out of scope for this read-only QA.

L6. **`extract_tour_references` will capture `BCCKG27-HU` even when `HU` standalone is correctly rejected.** This is the intended behaviour (the canonical real tour code includes the airline suffix), but it's worth being aware that the airline suffix is preserved inside multi-token codes — relevant if the future linker tries to match codes to canonical rows that store the airline suffix separately. Not a defect of this foundation.

L7. **`page_post_tour_links.tour_id` is intentionally NOT FK to `tours_canonical`.** Documented inline in the migration and in the Dev report. Acceptable for pre-canonical fire-sale codes; should be revisited if the future dashboard wants referential integrity at the FK level. No action needed for this task.

L8. **Cowork sandbox has no `.git` working tree.** Dev correctly stopped before committing/pushing and called this out. Codex (or Tiw on the real repo clone) must perform the commit/push to `v2/s4-followup-vision-ondemand`. This QA verdict assumes that commit will faithfully reflect the file paths reviewed above.

---

## 5. Tests Verified

QA executed the following directly inside the sandbox (against the same files Codex will read on the repo clone):

| Suite | Command | Result |
|-------|---------|--------|
| Targeted | `python -m pytest v2/tests/test_page_post_context.py` | `41 passed in 0.22s` |
| Broad non-live | `python -m pytest v2/tests/ --ignore=test_integration_staging.py --ignore=test_live_openai_health.py --ignore=test_phase2_live_followup.py` | `563 passed, 7 skipped` |

All 7 skips are pre-existing `test_webhook.py` cases gated on Flask install. They are unrelated to this task. Zero failures.

The 41 targeted tests are distributed across the 7 classes mapped to the 12 required behaviors:

- `TestUpsertIdempotency` (3 tests) → behavior 1
- `TestRecentWindow` (5 tests) → behavior 2
- `TestExtraction` (6 tests) → behaviors 3, 4
- `TestLinking` (4 tests) → behavior 5
- `TestMarkAndClearOverride` (7 tests) → behaviors 6, 7
- `TestBlocking` (7 tests) → behaviors 8, 9
- `TestSourceContext` (4 tests) → behavior 10
- `TestCompactContext` (2 tests) → behavior 11
- `TestLeakageSafety` (3 tests) → behavior 12

Coverage of the required behaviors is complete.

---

## 6. Remaining Risks

R1. **Real-Postgres partial unique enforcement is not exercised by unit tests.** The in-memory Supabase fake does not implement partial-unique index semantics. The application-layer code is structured to avoid duplicate inserts, so this is belt-and-braces, but a staging migration apply is required before any code path begins writing real rows. Out of scope for this QA — Codex should run the migration on staging Postgres before the next dependent task ships.

R2. **No live wholesale-blacklist drift detection.** The module imports `_WHOLESALE_BLACKLIST` from `v2.lib.response_writer`. If a future change to `response_writer.py` mutates that list, this module silently inherits the change. Acceptable because single-source-of-truth is the design goal, but worth a note for whoever next edits `response_writer.py`.

R3. **Source attribution depends on the future webhook task** filling `page_posts` rows before `get_source_context` runs. Until then, `source_type` will default to `unknown` even for genuine page-post arrivals. Dev flagged this; no action required at this layer.

R4. **`page_post_tour_links.tour_id` not FK-validated** at DB level. If the future dashboard writes a `tour_id` that does not match any canonical row, the system relies on the application layer to surface the error. Acceptable for the fire-sale pre-canonical use case.

R5. **`is_candidate_blocked` does not consult `page_post_tour_links`.** Today the candidate is identified by `web_code` / `tour_code_real` / `tour_id` / `page_post_id` passed directly by the caller. If the future response writer wants "any tour linked to this post inherits the post's sold-out status", that traversal must be added. Out of scope for this task; the post-scope override already covers the customer-arrived-from-a-full-post case.

---

## 7. Next Recommended Step

1. **Codex:** commit the four new/edited files to `v2/s4-followup-vision-ondemand` (Cowork sandbox has no git working tree) and tag the commit so this QA verdict is reproducible.
2. **Codex:** apply migration `20260519_020_page_post_intelligence.sql` on staging Postgres to confirm:
   - Idempotent re-apply (no-op on second run).
   - Partial unique indexes enforce the active-override single-row invariant.
   - RLS denies `anon`, allows `service_role`.
3. **Next Dev task (recommended):** wire `build_response_planning_context(...)` into `v2/lib/response_writer.py` so the LLM is gated by the deterministic block decision and receives only the compact source-context summary. Keep the change additive and behind a feature flag if any V1-compatible code path will read the new context.
4. **Following Dev task:** extend `v2/lib/admin_command_handler.py` with `posts`, `mark_full <code>`, and `clear_full <code>` commands that wrap the new module. Mirror the parser + leakage-control pattern used in `DEV-2026-05-19-005`.
5. **Future:** Meta webhook source-attribution wiring (`source_post_id` extraction from referral / ad / post payloads) so `get_source_context` has real data to operate on. Gate on the dashboard-auth decision before exposing read/write APIs.

---

Stopping here per `docs/AI_COMMAND_CENTER.md` Handoff Rule. Awaiting Codex review.
