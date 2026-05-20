# DEV-2026-05-20-015 — Dev Report

Status: `READY_FOR_QA`
Role: Claude Dev
Branch: `v2/s4-followup-vision-ondemand`
Base commit: `4ef8114`
Generated: 2026-05-20

---

## 1. Summary

Sprint 5 Package I closes the three data-quality risks QA-2026-05-20-014
flagged on the selected departure planning wiring:

1. The V2 listing scraper now emits `tours_canonical.url = /tour/<web_code>`
   instead of the legacy `/intertourdetail/<code>`. All V2 customer/admin
   canonical URLs match the real detail page (which the detail-page
   reader has used since DEV-012).
2. `tour_departures` rows now carry a deterministic `refreshed_at`
   freshness timestamp. The orchestrator's DB-first read path applies a
   configurable freshness TTL (default 6h) and refreshes stale rows
   exactly once per Redis-guarded window. Refresh failures fail closed:
   the bot keeps the stale rows but never fabricates a price or
   availability quote.
3. The non-unique `idx_dep_full_row` is left in place but is now
   accompanied by an audit helper and a documented (non-applied)
   uniqueness migration proposal. The proposal is gated behind a
   zero-duplicate audit.

A small offline-safe scheduled refresher CLI is also added under
`v2/tools/refresh_departure_rows.py` so operators can drive periodic
refreshes off the web_code list, the active `selected_tours` lock list,
or every stale tour.

All work is additive and V2-only. V1 production untouched. Make.com
stays deactivated. No production webhook settings, secrets, deploys,
live paid-provider calls, or Supabase migration applications were
performed.

## 2. Files changed

```
v2/scraper/scrape_tours.py                                    (modified - canonical URL fix)
v2/scraper/departure_price_table.py                           (modified - to_tour_departure_rows accepts refreshed_at)
v2/scraper/detail_enrichment.py                               (modified - upsert/enrich pin refreshed_at)
v2/lib/orchestrator.py                                        (modified - detail_freshness_ttl_s + stale-row gate)
v2/supabase/migrations/20260520_022_departure_refreshed_at.sql (new - additive, NOT applied)
v2/supabase/migrations/_pending_023_departure_unique.sql.proposal (new - gated UNIQUE proposal, not a .sql file)
v2/tools/refresh_departure_rows.py                            (new - offline-safe scheduled refresher CLI)
v2/tools/departure_duplicate_audit.py                         (new - duplicate audit helper + SQL constant)
v2/tests/conftest.py                                          (modified - make_tour url uses /tour/)
v2/tests/test_departure_freshness_and_audit.py                (new - 20 focused integration tests)
docs/V2_DEPARTURE_UNIQUENESS_PROPOSAL.md                      (new - audit + migration plan, no apply)
docs/tasks/DEV_REPORT_CURRENT.md                              (modified - this report)
docs/tasks/AGENT_STATUS.json                                  (modified - READY_FOR_QA)
```

No migration was applied. No V1 file was touched.

## 3. Implementation details

### 3.1 Canonical URL fix (sub-task 1)

`v2/scraper/scrape_tours.py` previously built the canonical URL as
`f"{BASE_URL}/intertourdetail/{code}"`. This is the legacy path that
returns 500 on production for the codes V2 cares about. Changed to
`f"{BASE_URL}/tour/{code}"`, matching the detail-page reader.

`v2/tests/conftest.py` `make_tour` fixture URL also updated so the
synthetic tour rows used across the entire V2 test suite match the new
canonical shape.

The existing `TOUR_LINK_RE` / `TOUR_LINK_ALT_RE` regexes still recognize
both `/intertourdetail/` and `/tour/` in incoming listing HTML — that's
deliberate so the parser keeps working against the real production
listing page (which still emits the legacy anchor href). Only the
*outgoing* canonical URL the bot stores/serves is normalized.

### 3.2 Freshness wiring (sub-task 2)

Migration 022 (`20260520_022_departure_refreshed_at.sql`):

- Additive: `ALTER TABLE tour_departures ADD COLUMN IF NOT EXISTS refreshed_at TIMESTAMPTZ`.
- Best-effort backfill via NULL-safe `COALESCE` from `updated_at` /
  `scraped_at` / `created_at` where those columns exist (each wrapped
  in `DO $$ ... EXCEPTION WHEN undefined_column THEN NULL; END $$` so
  the migration stays safe on databases that don't have the legacy
  audit columns).
- Adds a partial index `idx_dep_refreshed_at` for stale-row scans.
- NOT applied by Claude Dev — apply via the standard staging pipeline.

`v2.scraper.departure_price_table.to_tour_departure_rows` now accepts a
`refreshed_at: Optional[datetime]` kwarg and writes its ISO string into
every payload it produces. `v2.scraper.detail_enrichment.upsert_departure_rows`
defaults the stamp to "now" when callers omit it, and
`enrich_tour_detail` forwards its own `fetched_at` so tests can pin the
timestamp deterministically.

`v2.lib.orchestrator.Orchestrator` gains two new optional kwargs:

- `detail_freshness_ttl_s: int = 21600` (6h)
- `now=None` — injectable clock (defaults to `datetime.now(timezone.utc)`)

A new helper `_rows_are_stale` returns True when at least one DB row's
`refreshed_at` is older than the TTL. Legacy rows with NULL
`refreshed_at` are treated as fresh so V2 continues to work pre-022
application — the scheduled refresher backfills them over time.

`_get_or_fetch_departure_rows` now follows a freshness-aware
DB-first-then-HTTP-refresh strategy:

- Fresh DB rows → use directly, no HTTP fetch.
- Stale DB rows + cold Redis guard + http_client present → run one
  bounded `enrich_tour_detail` refresh, set the guard, return freshly
  parsed rows. If parse fails or HTTP raises, fall back to stale rows
  (fail closed). The Redis guard prevents repeat attempts inside the
  TTL window.
- Stale DB rows + hot guard → serve stale (do not loop).
- Stale DB rows + no http_client → serve stale (no live fetch).
- Empty DB + cold guard + http_client → original first-fetch path.

The safe planning bundle continues to instruct the LLM not to confirm
seat availability or final price. Stale-but-served rows still ride that
gate so customers never see a fabricated quote.

### 3.3 Scheduled refresher (sub-task 3)

New module `v2/tools/refresh_departure_rows.py`:

- Public API: `refresh_departure_rows(web_codes, *, supabase,
  http_client=None, dry_run=False, now=None, ttl_s=21600)` returning a
  `RefreshSummary` with per-web_code `RefreshOutcome`s.
- Audit helpers: `collect_stale_web_codes(supabase, ttl_s=, now=, limit=)`
  and `collect_selected_tour_web_codes(supabase, limit=)`.
- CLI entrypoint defaults to dry-run; the CLI itself does NOT wire a
  real Supabase or HTTP client — that's an operator-script
  responsibility, so an accidental `python -m v2.tools.refresh_departure_rows`
  invocation cannot hit the network or write to Supabase.
- Per-web_code actions: `skipped_dry_run`, `noop_fresh`, `refreshed`,
  `failed`, `no_http_client` — all surfaced on the summary.
- Refresh failure or non-parsed result keeps the existing rows
  untouched (fail closed). No retry loop.

### 3.4 Uniqueness readiness (sub-task 4)

New module `v2/tools/departure_duplicate_audit.py`:

- `find_duplicates(supabase) -> DuplicateAuditResult` aggregates by
  `(tour_id, departure_start, departure_end, COALESCE(bus, 0))` —
  the exact key the future UNIQUE index will use. Rows with NULL
  `departure_start` are excluded to mirror the proposed partial
  UNIQUE index.
- `DUPLICATE_AUDIT_SQL`: read-only SQL the operator can paste against
  staging to get the same answer without running Python.

`docs/V2_DEPARTURE_UNIQUENESS_PROPOSAL.md` documents the gate, the SQL,
the audit Python helper, and the future migration shape.

`v2/supabase/migrations/_pending_023_departure_unique.sql.proposal`
holds the exact SQL block we plan to apply once the audit returns zero
duplicates. The `.sql.proposal` suffix keeps it out of every `*.sql`
glob so no automation can accidentally apply it. Test
`test_uniqueness_proposal_is_not_a_sql_file` asserts this.

### 3.5 Tests

New test file `v2/tests/test_departure_freshness_and_audit.py` with 20
tests covering the required cases plus additional regression guards:

- Canonical URL: parsed listing, persisted canonical row, scraper
  source, conftest fixture.
- Freshness metadata: upsert stamping, enrich pinning.
- Orchestrator freshness gate: fresh rows skip HTTP, stale rows trigger
  one bounded refresh, refresh failure falls back to stale + no loop.
- Refresher: dry-run writes nothing, selected_tours collector,
  stale-only collector, per-web_code failure recording, no_http_client
  branch.
- Duplicate audit: finds duplicate group, safe when none, NULL-start
  exclusion, audit SQL is read-only and targets the correct key.
- Migration files: 022 exists + DDL has no UNIQUE keyword; proposal is
  not a `.sql` file.

## 4. Migration notes

- `20260520_022_departure_refreshed_at.sql` is **NOT applied by Claude
  Dev**. The migration is additive (ADD COLUMN IF NOT EXISTS + partial
  index + NULL-safe COALESCE backfill from existing audit columns).
  Apply via the standard staging pipeline once QA-2026-05-20-015 is
  GO.
- `_pending_023_departure_unique.sql.proposal` is **deliberately not a
  `.sql` file** so no migration runner picks it up. It must only be
  applied after `v2.tools.departure_duplicate_audit.find_duplicates`
  reports `safe_for_unique_index == True` on staging.

## 5. Test results

All runs offline. No live network, no live LLM, no live Supabase, no
live LINE/Meta/OCR/paid-provider calls.

### Targeted suite (DEV-015 + adjacency)

```
pytest v2/tests/test_detail_enrichment.py \
       v2/tests/test_selected_departure_planning.py \
       v2/tests/test_departure_freshness_and_audit.py \
       --basetemp=.pytest_tmp -p no:cacheprovider -q
```

Result: `62 passed in 0.33s` (20 new + 26 selected_departure_planning + 16 detail_enrichment).

### Broad non-live V2 suite

```
pytest v2/tests \
       --ignore=v2/tests/test_integration_staging.py \
       --ignore=v2/tests/test_live_openai_health.py \
       --ignore=v2/tests/test_phase2_live_followup.py \
       --basetemp=/tmp/pyt -p no:cacheprovider -q
```

Result: `807 passed, 40 skipped, 0 failed in 2.23s` (was 783 in
DEV-014; +24 reflects new tests + the orchestrator/scheduler additions
covered by existing scenarios).

Note: the broad suite produces `PermissionError` errors when pytest's
default basetemp lives under the OneDrive-synced workspace directory
(`v2/.pytest_tmp`). Per CURRENT_DEV_TASK.md guidance, re-running with
an external basetemp (`/tmp/pyt`) clears the errors — they were OS-level
filesystem permission issues on the OneDrive mount, never test code
failures.

Skips are exclusively flask-only tests where the optional `flask`
package is not installed in the sandbox.

## 6. Safety / scope guard verification

- V1: untouched. No file under `legacy/` / `v1/` was modified.
- Make.com: untouched. Scenario 4967547 stays deactivated.
- Deploy: none.
- Production Meta webhook settings: untouched.
- Secrets: none read, printed, rotated, or persisted.
- Live paid providers: zero calls. No OpenAI / Anthropic / OCR /
  Document AI / Meta / LINE traffic.
- Supabase migrations: 022 authored but NOT applied. Proposal 023 is
  deliberately suffixed `.sql.proposal` so no `*.sql` glob picks it up.
- Customer-wide traffic: not enabled. Admin-only test posture preserved.
- Wholesale partner names: never appear in any returned string or test
  fixture. Defense-in-depth `_strip_wholesale` still runs over the
  selected_departure_planning payload (unchanged from DEV-014).
- `web_code` / `tour_code_real` / `airline`: still kept strictly
  separate in the canonical URL fix, the freshness wiring, and the
  refresher (the refresher's payload is the parsed
  `DeparturePriceRow`, which preserves them).
- Refresh failure: fail closed. Existing rows untouched, no fabricated
  quote, no retry loop. Asserted by tests.
- Past dates and `-`/None values: behavior unchanged from DEV-013/014;
  refresher does not touch these invariants.

## 7. Known notes / risks

- Migration 022's `refreshed_at` is nullable and the orchestrator
  treats NULL `refreshed_at` as fresh (legacy rows). This is by design
  so V2 keeps working before migration 022 is applied on staging.
  Operators should run the scheduled refresher once 022 is live to
  backfill the column quickly.
- The Redis guard `detail_fetched:{web_code}` (default 300s TTL) is
  separate from the freshness TTL (default 6h). The guard prevents a
  refresh storm inside its window; the freshness TTL governs when a
  refresh is needed at all. Operators can tune both via Orchestrator
  kwargs.
- The duplicate audit is purely advisory in this task. It does NOT
  delete rows or apply the UNIQUE migration. Owner of the next step is
  Codex + Tiw.
- The scheduled refresher's CLI is intentionally inert (no auto-wired
  Supabase / HTTP client) to avoid an accidental staging hit. Operators
  must wire their own clients into a one-line script.
- The orchestrator's stale-row fallback serves stale rows when the
  refresh fails. The LLM still receives the deterministic
  `safe_planning_note` saying "do not confirm seat availability or
  final price" — but downstream reviewers should confirm that note
  copy is still appropriate when the data is known stale.

## 8. Exact QA focus areas

QA should verify in this order:

1. Code review of:
   - `v2/scraper/scrape_tours.py` — canonical URL builder uses `/tour/`,
     `TOUR_LINK_*` regexes intentionally still match legacy listing
     anchors, no V2 customer/admin link path uses
     `/intertourdetail/`.
   - `v2/scraper/detail_enrichment.py` + `v2/scraper/departure_price_table.py`
     — `refreshed_at` flows from `enrich_tour_detail.fetched_at` through
     `upsert_departure_rows` into the payload.
   - `v2/lib/orchestrator.py` — `_rows_are_stale` semantics (one stale
     row trips refresh; NULL = fresh), `_get_or_fetch_departure_rows`
     freshness branches, refresh failure fall-back path, guard
     interactions.
   - `v2/tools/refresh_departure_rows.py` — dry-run never writes;
     refresh failure marks `failed`; no retry; bounded by `--limit`;
     CLI does not wire live clients.
   - `v2/tools/departure_duplicate_audit.py` — pure read; key matches
     the proposed UNIQUE-index key; NULL-start excluded.
   - `v2/supabase/migrations/20260520_022_departure_refreshed_at.sql`
     — additive, idempotent, no UNIQUE in DDL.
   - `v2/supabase/migrations/_pending_023_departure_unique.sql.proposal`
     — not in `*.sql` glob; transactionally safe.
2. Run the targeted suite:
   - `pytest v2/tests/test_detail_enrichment.py v2/tests/test_selected_departure_planning.py v2/tests/test_departure_freshness_and_audit.py --basetemp=/tmp/pyt -p no:cacheprovider -q`
3. Run the broad non-live V2 suite (with an external basetemp to avoid
   the OneDrive permission issue):
   - `pytest v2/tests --ignore=v2/tests/test_integration_staging.py --ignore=v2/tests/test_live_openai_health.py --ignore=v2/tests/test_phase2_live_followup.py --basetemp=/tmp/pyt -p no:cacheprovider -q`
4. Scope discipline check:
   - No V1 / Make.com / migration apply / deploy / production webhook /
     secret changes.
   - No live paid-provider calls anywhere in the new test runs.
5. Verify proposal-file gating:
   - `ls v2/supabase/migrations/*.sql` does NOT include
     `_pending_023_departure_unique.sql.proposal`.

## 9. Recommendation

`GO` — all 20 new tests pass, 807-test broad non-live suite green with
zero failures, hard rules honored, migration 022 ready for the standard
staging pipeline, UNIQUE-promotion proposal cleanly gated behind the
duplicate audit. Owner (Tiw) approval remains required before applying
migration 022 to staging and before any production webhook / live LLM
/ customer-wide traffic step.
