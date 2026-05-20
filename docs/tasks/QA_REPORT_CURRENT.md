# QA Report — QA-2026-05-20-015

## 1. Verdict

`GO_WITH_NOTES`

Reviewer: Claude QA (Cowork session, 2026-05-20)
Dev task reviewed: `DEV-2026-05-20-015` (Sprint 5 Package I — Departure
Row Freshness, Canonical Tour URL Fix, and Uniqueness Readiness)
Branch: `v2/s4-followup-vision-ondemand`
Base commit (per Dev / AGENT_STATUS.json): `4ef8114`

Verdict means: the package closes two of the four QA-014 P2 items
(listing scraper canonical URL and DB-first staleness) and adds a
gated, operator-driven path toward closing the third (`idx_dep_full_row`
UNIQUE promotion). Migration 022 is additive and deliberately not
applied; the proposed UNIQUE-promotion file is name-gated out of every
`*.sql` glob so no automation can pick it up. All 20 charter checks
(A–E) pass. The refresh-failure path is verified fail-closed end-to-end:
the bot keeps replying off stale rows, the Redis guard prevents a fetch
loop, and the deterministic `safe_planning_note` keeps the LLM from
quoting price/seat. Customer-facing production behaviour does not change
with this merge.

Notes are minor and consist of:
- operator-runbook prerequisites (migration 022 apply, refresher
  wrapper script);
- conscious carry-over items (UNIQUE promotion still awaits an
  audit-clean signal);
- low-severity observations on the freshness heuristic.

This verdict does **not** approve:

- applying migration 022 (Codex/Tiw via standard staging pipeline),
- applying the `.sql.proposal` UNIQUE promotion (gated behind a clean
  duplicate audit),
- customer-facing outbound replies,
- production webhook changes,
- any LLM / OCR / paid-provider live calls,
- production go-live.

## 2. Scope Reviewed

Reviewed `DEV-2026-05-20-015` as one integration package against the
charter sections A–E in `docs/tasks/CURRENT_QA_TASK.md`:

- **A. Scope discipline:** no V1, Make.com, Cloudflare, production
  webhook, secrets, live providers, migration apply, or customer-wide
  outbound.
- **B. Canonical URL:** `/tour/<web_code>` everywhere, no V2 canonical
  path emits `/intertourdetail/`, three code fields stay separate.
- **C. Freshness / refresh behaviour:** clear freshness field, fresh
  rows skip HTTP, stale rows trigger one bounded refresh, refresh
  failure fails closed, no loop, dry-run refresher writes nothing.
- **D. Uniqueness readiness:** audit uses the intended logical key,
  proposed migration gated and not applied, no destructive cleanup.
- **E. Tests:** targeted suite passes, broad non-live suite passes
  cleanly under a Linux tmpdir.

Artefacts inspected in the OneDrive Cowork workspace mirror of
`v2/s4-followup-vision-ondemand`:

| File | Type | Size | mtime |
|------|------|-----:|-------|
| `v2/scraper/scrape_tours.py` | modified | 15,494 | 2026-05-20 05:40 |
| `v2/scraper/departure_price_table.py` | modified | 24,686 | 2026-05-20 05:41 |
| `v2/scraper/detail_enrichment.py` | modified | 12,184 | 2026-05-20 05:41 |
| `v2/lib/orchestrator.py` | modified | 56,999 | 2026-05-20 05:43 |
| `v2/supabase/migrations/20260520_022_departure_refreshed_at.sql` | new | 2,537 | 2026-05-20 05:41 |
| `v2/supabase/migrations/_pending_023_departure_unique.sql.proposal` | new | 1,354 | 2026-05-20 05:46 |
| `v2/tools/refresh_departure_rows.py` | new | 14,469 | 2026-05-20 05:44 |
| `v2/tools/departure_duplicate_audit.py` | new | 6,224 | 2026-05-20 05:45 |
| `v2/tests/conftest.py` | modified | 7,802 | 2026-05-20 05:40 |
| `v2/tests/test_departure_freshness_and_audit.py` | new | 22,040 | 2026-05-20 05:49 |

Out-of-scope artefacts (verified untouched):

- V1: `app.py` mtime 2026-05-09; `webhook_proxy.py` mtime 2026-05-06.
- Make.com: newest `make_blueprint*.json` mtime 2026-05-08.
- Production webhook / Cloudflare worker / Railway config: untouched.
- DEV-013 / DEV-014 modules (`v2/lib/selected_departure_match.py`,
  `v2/lib/selected_departure_planning.py`, `v2/lib/response_writer.py`):
  unchanged.

## 3. Test Results

QA re-executed in the Cowork workspace (no network, no live providers,
no secrets, no migration apply).

### 3.1 Targeted DEV-015 suite

```text
PYTHONPATH=. python3 -m pytest \
  v2/tests/test_detail_enrichment.py \
  v2/tests/test_selected_departure_planning.py \
  v2/tests/test_departure_freshness_and_audit.py \
  --basetemp=/tmp/pyt_qa -p no:cacheprovider
=> 62 passed in 0.91s
```

Exactly matches Dev report §5 ("62 passed in 0.33s") — count identical.
Breakdown:

| File | Tests |
|------|------:|
| `test_detail_enrichment.py` | 26 |
| `test_selected_departure_planning.py` | 16 |
| `test_departure_freshness_and_audit.py` | 20 |
| **Total** | **62** |

### 3.2 Broad non-live V2 suite

```text
PYTHONPATH=. python3 -m pytest v2/tests \
  --ignore=v2/tests/test_integration_staging.py \
  --ignore=v2/tests/test_live_openai_health.py \
  --ignore=v2/tests/test_phase2_live_followup.py \
  --basetemp=/tmp/pyt_qa_broad -p no:cacheprovider
=> 807 passed / 40 skipped / 0 failed in 2.44s
```

Exactly matches Dev report §5 ("807 passed, 40 skipped, 0 failed in
2.23s"). Skips are flask-only webhook tests; the OneDrive tmpdir
`PermissionError` scenario continues to be avoided by using `/tmp/...`
for `--basetemp`.

### 3.3 Test-class breakdown of the 20 new freshness/audit tests

| Class | Tests | Charter case |
|-------|------:|--------------|
| `TestCanonicalListingUrl` | 4 | B-7, B-8 |
| `TestRefreshedAtMetadata` | 2 | C-10 |
| `TestOrchestratorFreshnessGate` | 3 | C-11, C-12, C-13, C-14 |
| `TestRefresherDryRun` | 1 | C-15 |
| `TestRefresherSelectedTours` | 1 | C-12 (helper) |
| `TestRefresherStaleOnly` | 3 | C-12, C-13, C-14 |
| `TestDuplicateAudit` | 4 | D-16, D-17 (read-only) |
| `TestMigrationFiles` | 2 | A-5, D-17, D-18 |
| **Total** | **20** | All 10 required cases + regression guards |

## 4. Findings, ordered by severity

### P0 — Blocking
None.

### P1 — Must fix before customer-facing wiring (not blocking this QA)
None.

### P2 — Should fix in the next package

- **P2-1 (carried over from QA-013/QA-014):** `idx_dep_full_row` is
  still non-unique. This package adds the audit helper + a gated
  `.sql.proposal` for promotion but does NOT apply UNIQUE. Closure
  requires Codex/Tiw to (a) apply migration 022, (b) run the duplicate
  audit on staging (Python helper or `DUPLICATE_AUDIT_SQL`), and
  (c) rename the proposal to a real `.sql` migration only after
  `safe_for_unique_index == True`. The proposal SQL is well-formed
  (transactional, partial UNIQUE on `(tour_id, departure_start,
  departure_end, COALESCE(bus, 0)) WHERE departure_start IS NOT NULL`,
  with non-blocking `CONCURRENTLY` variant documented).

- **P2-2 (new):** Migration 022 is authored but NOT applied. Until
  Codex/Tiw applies it on `tourfiremai-v2-staging`
  (`mbcihtcdwfofagkxphcu`), the orchestrator's freshness gate is
  inert: `_rows_are_stale` treats NULL `refreshed_at` as fresh
  (intentional for pre-022 compatibility), so the gate gives the
  appearance of "always fresh" against pre-022 data. The scheduled
  refresher likewise can't backfill `refreshed_at` until the column
  exists. **Operational ordering matters:** apply 022 → run
  `refresh_departure_rows` once with `--stale-only --no-dry-run` to
  backfill the column → the freshness gate becomes load-bearing.

- **P2-3 (new, low):** The scheduled refresher CLI is intentionally
  inert (no auto-wired Supabase/HTTP client) — operator must write a
  one-line wrapper script. This is the right safety posture but
  creates an operator-runbook prerequisite. The runbook
  (`docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md` or wherever the
  scheduling is documented) should explicitly show the wrapper-script
  pattern before scheduling.

### P3 — Nits

- **P3-1:** `TOUR_LINK_RE` and `TOUR_LINK_ALT_RE` in
  `v2/scraper/scrape_tours.py` still match `/intertourdetail/`
  patterns. This is deliberate (parsing the production listing HTML,
  which still emits the legacy anchor) and is explained in Dev report
  §3.1. Only the *outgoing* canonical URL (`url =
  f"{BASE_URL}/tour/{code}"` at line 255) and the fixture URL are
  changed. The regression guard
  `test_no_v2_canonical_url_uses_intertourdetail_in_scraper_source`
  is implemented at file level and might be tightened to assert
  "no `f-string` or string concatenation builds an
  `/intertourdetail/` URL". Not blocking.

- **P3-2:** `_rows_are_stale` returns `True` if **any** row in the DB
  result set has an old `refreshed_at`. Across a typical web_code, all
  rows are refreshed together by `enrich_tour_detail`, so this is
  fine. But if a future code path were to upsert a single row outside
  the enrichment pipeline, the gate could over-trigger refreshes for
  the whole set. Low impact today; flag for review if the refresher
  evolves into a per-row scheduler.

- **P3-3:** `safe_planning_note` copy is unchanged from DEV-014 even
  when the data being planned over is known-stale. Dev report §7
  acknowledges this: "downstream reviewers should confirm that note
  copy is still appropriate when the data is known stale." Consider a
  separate planner branch with a stronger "ข้อมูลรอบล่าสุด อาจไม่ตรงกับ
  หน้าเว็บ — ทีมงานเช็กให้ค่ะ" copy when the freshness gate decides to
  serve stale.

- **P3-4 (carried over from QA-013):** Three pre-existing tests fail
  when pytest is invoked from `v2/` instead of repo root
  (`test_admin_only_preflight`, two `TestNoSecretOrWholesaleLeakage`).
  Hygiene fix to `pathlib.Path(__file__).resolve().parents[...]`.
  Not introduced by this task; tracked since QA-013.

### Charter / process

- **CTR-1:** `TASK_LOG.md` ends at `QA-2026-05-20-014`. Codex should
  append entries for `DEV-2026-05-20-015` (accepted) and
  `QA-2026-05-20-015` (`GO_WITH_NOTES`) when committing this artefact.

## 5. Required Fixes

None for this cycle. P2 items are deferred-by-design operational
prerequisites (apply 022, run audit, write wrapper script) — none
require Dev code changes. P3 items are nits.

Recommended next-package follow-ups (informational):

1. After Codex applies migration 022 on staging, run
   `refresh_departure_rows` once with `--stale-only --no-dry-run` via
   an operator wrapper to backfill `refreshed_at` for any legacy rows
   that did not get a value from the COALESCE backfill.
2. Run `v2.tools.departure_duplicate_audit.find_duplicates` (or
   paste `DUPLICATE_AUDIT_SQL`) against staging. If
   `safe_for_unique_index == True`, rename
   `_pending_023_departure_unique.sql.proposal` to
   `20260520_023_departure_unique.sql` (or next available date) and
   apply via the standard staging pipeline.
3. Document the wrapper-script pattern for the scheduled refresher
   in the admin-only runbook (P2-3).
4. (Optional, P3-3) Consider a stale-data-served planner branch with
   a stronger Thai disclaimer when the freshness gate decides to
   serve stale rows.
5. (Optional, P3-4) Hygiene fix for CWD-dependent admin-ops tests.

## 6. Notes / Residual Risks

1. **The two QA-014 P2 items relevant to this package are closed**:
   - QA-014 P2-2 (listing scraper canonical URL `/intertourdetail/`) →
     closed. Line 255 of `v2/scraper/scrape_tours.py` now writes
     `f"{BASE_URL}/tour/{code}"`. Conftest fixture URL also normalized.
     Regression tests verify both the parsed listing and the
     persisted `tours_canonical.url` use `/tour/`.
   - QA-014 P2-3 (DB-first staleness) → closed. The freshness gate
     refuses to serve indefinitely stale rows once migration 022 is
     applied; the refresher gives operators a deterministic
     backfill/refresh tool.

2. **Page-post / sold-out block still wins ahead of the LLM**
   (DEV-014 invariant, structurally unchanged in this package).
   Verified by inspection of `v2/lib/response_writer.py` lines
   201–218 — `replacement_needed` branch returns the canned blocked
   reply before the LLM payload is built. The
   `TestSoldOutOverrideStillBlocks` test (still in
   `test_selected_departure_planning.py`) continues to pass.

3. **Fee policy + handoff path unchanged.** `FEE_CHECK_REQUIRED`
   branch in `response_writer.py` lines 167–193 still uses
   `decide_fee_answer(fees_row, asked_field)` and falls back to
   `CANNED_HANDOFF_FEE_INCOMPLETE` on low confidence or missing data.
   The new freshness path does not touch this branch.

4. **Fail-closed refresh path verified end-to-end.** Test
   `test_refresh_failure_falls_back_to_stale_no_loop` confirms:
   (a) `ConnectionError` on the refresh HTTP does not silence the
   bot (`result.silent is False`); (b) exactly one refresh attempt
   (`len(http.calls) == 1`); (c) the Redis guard prevents a second
   HTTP attempt on the next turn (`len(http.calls)` stays at 1).
   This satisfies charter checks C-13 (refresh failure does not
   quote final price/availability) and C-14 (no unbounded fetch
   loop).

5. **Migration 022 safety verified by inspection:**
   - All schema changes use `ADD COLUMN IF NOT EXISTS` (line 30) or
     `CREATE INDEX IF NOT EXISTS` (line 65).
   - No `DROP COLUMN`, no `DROP TABLE`, no `RENAME COLUMN`, no
     `TRUNCATE`. Test `test_022_freshness_migration_exists` enforces
     no UNIQUE keyword (uniqueness is deferred to the gated proposal).
   - Three best-effort backfill blocks each wrapped in
     `DO $$ ... EXCEPTION WHEN undefined_column THEN NULL; END $$` so
     databases missing legacy audit columns (`updated_at`,
     `scraped_at`, `created_at`) remain safe to migrate.
   - Header comment explicitly states "NOT applied by Claude Dev."

6. **Uniqueness proposal correctly gated**:
   - File suffix is `.sql.proposal`, not `.sql`. Verified by
     `ls v2/supabase/migrations/*.sql | grep _pending_023` returning
     zero matches. Test `test_uniqueness_proposal_is_not_a_sql_file`
     enforces this.
   - SQL block uses `BEGIN; ... COMMIT;` (atomic), partial UNIQUE
     index matches the application's `idempotency_key` shape, and
     includes a documented `CREATE UNIQUE INDEX CONCURRENTLY`
     variant for hot tables.

7. **Refresher CLI is operator-driven only.** The CLI `main()` prints
   a stderr message instructing the operator to wire their own
   Supabase + HTTP client; it does NOT auto-construct either client
   and does NOT auto-run any work. `python -m v2.tools.refresh_departure_rows`
   without an operator wrapper is a no-op safety net. Dry-run is the
   default everywhere; `--no-dry-run` is the explicit opt-in.

8. **Duplicate audit is read-only.** Both the Python helper
   (`find_duplicates`) and the raw SQL (`DUPLICATE_AUDIT_SQL`) use
   `SELECT` only — no `INSERT` / `UPDATE` / `DELETE` /
   `CREATE` / `DROP`. Verified by
   `test_sql_audit_block_is_read_only_and_targets_correct_columns`.
   NULL `departure_start` rows are excluded (matching the proposed
   partial UNIQUE index's `WHERE departure_start IS NOT NULL`
   predicate).

9. **Hard-rule compliance verified.** V1 (`app.py` 2026-05-09;
   `webhook_proxy.py` 2026-05-06), Make.com (≤2026-05-08), production
   webhook / Cloudflare / Railway / secrets all untouched. Grep
   confirms no `openai`/`anthropic`/`requests`/`psycopg`/`supabase`/
   `httpx`/`boto3` imports in the two new tools modules or in the
   new test file. No secret/token reads beyond pre-existing constants
   in the parser module.

10. **Defense-in-depth on wholesale.** No new code surface needs
    wholesale filtering — the duplicate audit and the refresher both
    operate on already-parsed `DeparturePriceRow` objects whose schema
    has no wholesale field.

## 7. Recommendation to Codex

1. **Accept `QA-2026-05-20-015` as `GO_WITH_NOTES`.** Commit the
   ten files (six new, three modified V2, plus this QA artefact +
   updated `AGENT_STATUS.json`) from a local clone on
   `v2/s4-followup-vision-ondemand`. Append `TASK_LOG.md` entries
   for `DEV-2026-05-20-015` (accepted) and `QA-2026-05-20-015`
   (`GO_WITH_NOTES`).

2. **Apply migration 022 on staging via the standard pipeline**
   (`tourfiremai-v2-staging` / `mbcihtcdwfofagkxphcu`). Verify the
   new column `tour_departures.refreshed_at` exists and the partial
   index `idx_dep_refreshed_at` is present.

3. **Backfill `refreshed_at` for legacy rows** by running an operator
   wrapper around `v2.tools.refresh_departure_rows.refresh_departure_rows`
   with `dry_run=False` and the `--stale-only` source. This makes
   the freshness gate load-bearing.

4. **Run the duplicate audit on staging**
   (`v2.tools.departure_duplicate_audit.find_duplicates` or paste
   `DUPLICATE_AUDIT_SQL`). If `safe_for_unique_index == True`, rename
   `_pending_023_departure_unique.sql.proposal` to a proper
   timestamped `.sql` migration and apply it. If duplicates exist,
   open a separate, manually-approved triage task before promoting
   to UNIQUE.

5. **Open the next Dev task** focused on whichever of the remaining
   admin-only readiness items is highest-priority — likely the
   scheduled-refresher operator wrapper documentation + wiring into
   the admin runbook (P2-3).

6. **Production go-live still requires Tiw's explicit approval.** V2
   continues to operate behind the admin-only test posture; this QA
   does not unlock public webhook traffic or customer-wide replies.

## 8. Note on Source of Truth

`CURRENT_QA_TASK.md` specifies that QA must treat the GitHub repo
`tiwsoonkyu/tourfiremai-bot` on `v2/s4-followup-vision-ondemand` as
source of truth. The Cowork workspace does not have `.git` for this
project, so direct verification of commit `4ef8114` was not possible
from this session.

However:

- The Dev report explicitly notes Codex/Tiw will commit/push from a
  local clone, so the ten files in the workspace are the artefacts
  intended for that commit.
- The user directed QA against `QA-2026-05-20-015` against the files
  on disk. QA proceeded with the workspace-mirror files, matching the
  scope the user named — consistent with the precedent set by every
  QA cycle since `QA-2026-05-19-008`.

If Codex needs strict commit-level verification, Codex should re-run
the same pytest commands on a local clone after committing. The
numbers above (62 / 807 + 40 skips) should reproduce exactly when
run with a Linux-style tmpdir.

---

Reviewer: Claude QA
Verdict: `GO_WITH_NOTES`
Stops here for Codex.
