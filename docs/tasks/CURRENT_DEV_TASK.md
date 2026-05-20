# DEV-2026-05-20-015 — Sprint 5 Package I

## Title

Departure-row freshness, canonical tour URL fix, and uniqueness readiness.

## Status

`PENDING`

## Assigned Role

Claude Dev

## Controller

Codex

## Branch

`v2/s4-followup-vision-ondemand`

## Background

QA-2026-05-20-014 returned `GO_WITH_NOTES` for selected departure planning. No P0/P1 issues remain, but QA identified three data-quality risks that should be closed before admin-only real-chat testing depends on departure rows:

1. `tour_departures` rows can become stale because the orchestrator currently uses DB-first reads indefinitely once any row exists.
2. Listing scraper canonical URLs still use legacy `/intertourdetail/<code>` instead of the real `/tour/<web_code>` path.
3. `idx_dep_full_row` is still non-unique. It should become a true idempotency guarantee only after a duplicate/backfill audit.

## Business Goal

Make V2 departure-row data reliable enough for admin-only chat testing:

- detail-page rows should have a freshness policy;
- stale rows should be refreshed deterministically, not guessed;
- customer/admin-facing links should point to the real website URL;
- duplicate departure rows should be prevented only after a safe audit path.

## Scope

Implement this as one integration package. Do not stop after each small subtask unless a P0 risk appears.

### 1. Canonical Listing URL Fix

Fix the V2 listing scraper so `tours_canonical.url` uses the real detail URL:

- Expected: `https://www.tourfiremai.com/tour/<web_code>`
- Not allowed: `/intertourdetail/<code>`

Requirements:

- Keep `web_code`, `tour_code_real`, and airline separate.
- Do not change V1 scraper or V1 production code.
- Add/update tests proving `tours_canonical.url` is `/tour/<web_code>`.
- Add a regression test proving no V2 customer/admin link path uses `/intertourdetail/` for canonical tour URLs.

### 2. Departure Row Freshness

Add a freshness mechanism for `tour_departures` rows.

Requirements:

- Inspect existing migration 021 schema first.
- If needed, create an additive migration 022 that adds a nullable `refreshed_at timestamptz` or equivalent freshness field to `tour_departures`.
- Do not apply the migration from Claude Dev.
- Update detail-enrichment/upsert logic so newly parsed rows write the freshness timestamp.
- Update orchestrator/detail-row read logic so stale rows can trigger a refresh according to a configurable TTL.
- Default TTL should be conservative for tour pricing, e.g. 6 hours for normal staging use, but testable via parameter/env/config.
- If refresh fails, fail closed: do not invent availability or final price; keep existing handoff/confirmation behavior.
- Add tests covering fresh rows, stale rows, refresh success, refresh failure, and no unbounded repeated fetches.

### 3. Scheduled Refresher Foundation

Add a small offline-safe scheduler/CLI foundation for refreshing departure rows for selected tours.

Requirements:

- A CLI or callable function may be added under `v2/tools/` or `v2/scraper/`.
- It must be safe by default and support dry-run mode.
- It should refresh by `web_code` list or recently-used/selected tours when a DB client is provided.
- Unit tests must use fakes/mocks only; no live network or Supabase calls.

### 4. Uniqueness Readiness

Prepare, but do not dangerously force, DB uniqueness.

Requirements:

- Add a duplicate-audit SQL query or helper that finds duplicate logical rows for the intended `idx_dep_full_row` key.
- Add a migration or documented SQL block for the future UNIQUE index only if safe, or clearly gate it behind the audit returning zero duplicates.
- Do not drop rows or mutate existing data in this task.
- Do not apply migrations from Claude Dev.

### 5. Tests

Add focused tests for the package.

Required cases:

1. Listing scraper canonical URL is `/tour/<web_code>`.
2. No V2 canonical tour URL uses `/intertourdetail/`.
3. Upserted departure rows carry freshness metadata.
4. Fresh DB rows do not trigger HTTP detail fetch.
5. Stale DB rows trigger one bounded refresh.
6. Refresh failure does not quote final price/availability and does not loop endlessly.
7. Dry-run refresher does not write to DB.
8. Duplicate-audit helper identifies duplicates with the intended logical key.
9. Proposed uniqueness migration is additive/safe and not applied in tests.
10. Broad non-live V2 suite remains green.

Run at minimum:

```bash
pytest v2/tests/test_detail_enrichment.py v2/tests/test_selected_departure_planning.py -q
pytest v2/tests --ignore=integration --ignore=live --ignore=v2/tests/test_live_openai_health.py --basetemp=.pytest_tmp -p no:cacheprovider -q
```

## Out of Scope

- Do not deploy.
- Do not modify V1.
- Do not modify Make.com.
- Do not change production Meta webhook settings.
- Do not apply Supabase migrations.
- Do not add live paid provider calls.
- Do not run live Meta / LINE / OpenAI / OCR / paid-provider calls.
- Do not enable customer-wide traffic.
- Do not build dashboard UI in this task.
- Do not alter fee policy thresholds.

## Deliverables

- V2-only code/tests/migrations as needed.
- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

## Required Dev Report

Write `docs/tasks/DEV_REPORT_CURRENT.md` with:

1. Summary
2. Files changed
3. Implementation details
4. Migration notes, if any, including "not applied"
5. Test results
6. Safety / scope guard verification
7. Known notes / risks
8. Exact QA focus areas
9. Recommendation: `GO`, `GO_WITH_NOTES`, or `NO_GO`

Update `docs/tasks/AGENT_STATUS.json` to:

- `status`: `READY_FOR_QA`
- `current_dev_task`: `DEV-2026-05-20-015`
- `current_qa_task`: `QA-2026-05-20-015`
- `next_action`: `CLAUDE_QA_RUN_CURRENT_QA_TASK`

Then stop.
