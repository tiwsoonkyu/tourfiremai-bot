# Task Log

This file tracks coordination between Codex and Claude Cowork.

Append only. Do not delete old entries.

## 2026-05-19

### `S4-LIVE-DEV-2026-05-18-001`

Status: `BLOCKED_SUPERSEDED`

Summary:

Live OpenAI measurement cannot be run inside Claude Cowork because the Cowork sandbox does not inherit Tiw's local shell secrets. The task is also superseded by later follow-up commits on `v2/s4-followup-vision-ondemand`.

Report path:

- `docs/tasks/DEV_REPORT_CURRENT.md`

Next action:

Codex issues a fresh non-live Dev task for optional OCR provider abstraction and benchmark readiness.

### `DEV-2026-05-19-003`

Status: `QA_GO`

Goal:

Add optional paid OCR / Document parser provider abstraction and benchmark harness for PDF fee accuracy, without live paid-provider calls.

Result:

- Provider abstraction and benchmark harness implemented.
- Cleanup notes L1/L2 closed.
- QA verdict: `GO`.
- Latest QA status commit observed by Codex: `4154172`.

### `QA-2026-05-19-003`

Status: `QA_GO`

Goal:

Review Dev output for OCR provider abstraction, benchmark readiness, safety thresholds, no live paid calls, and scope discipline.

Result:

- `docs/tasks/QA_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

### `DEV-2026-05-19-004`

Status: `QA_GO_WITH_NOTES`

Goal:

Build the V2 Admin Handoff + Memory Control foundation so admin/dashboard tools can inspect cases, pause bot handling, resume bot handling, and preserve selected-tour context.

Expected deliverables:

- V2-only code/tests/docs changes
- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

### `QA-2026-05-19-004`

Status: `QA_GO`

Goal:

Review Dev output for admin handoff, bot pause/resume, memory continuity, dashboard-safe case summaries, and scope discipline.

Result:

- `docs/tasks/QA_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`
- QA verdict: `GO`.
- Latest QA status commit observed by Codex: `af6d3e9`.

### `DEV-2026-05-19-005`

Status: `READY_FOR_QA`

Goal:

Wire the QA-cleared admin_ops foundation into a deterministic LINE admin command handler core so staff can list cases, inspect a case, pause the bot, and resume the bot without live LINE API calls.

Expected deliverables:

- V2-only code/tests/docs changes
- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Result:

- Added `v2/lib/admin_command_handler.py`
- Added `v2/tests/test_admin_command_handler.py`
- Dev recommendation: `GO`
- Tests: 538 passed, 11 skipped, 0 failed

### `QA-2026-05-19-005`

Status: `QA_GO_WITH_NOTES`

Goal:

Review Dev output for the LINE admin command handler core, including parser coverage, admin_ops integration, pause/resume safety, and leakage controls.

Expected deliverables:

- `docs/tasks/QA_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Result:

- QA verdict: `GO_WITH_NOTES`.
- All 15 QA scope checks passed.
- Follow-up notes:
  - Add explicit pause/resume redaction tests before wiring the real LINE adapter.
  - Future LINE adapter must enforce a staff allow-list before forwarding admin commands.
  - Codex should run a broad non-live suite on the real repo clone as post-QA sanity.

### `DEV-2026-05-19-006`

Status: `READY_FOR_QA`

Goal:

Build the V2 Page Post Intelligence + Sold-Out Signal foundation so the AI can use recent page-post context and respect admin full/sold-out signals before recommending tours.

Expected deliverables:

- Additive V2 migration(s) for page posts, post-tour links, and availability overrides.
- Deterministic V2 service module for recent post context and sold-out decisions.
- Unit tests for 3-day memory, tour-code extraction, source context, and sold-out blocking.
- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Result:

- Added `v2/supabase/migrations/20260519_020_page_post_intelligence.sql`
- Added `v2/lib/page_post_context.py`
- Added `v2/tests/test_page_post_context.py`
- Updated `docs/V2_PAGE_POST_INTELLIGENCE_PLAN.md` and `docs/V2_DATA_MODEL.md`
- Dev recommendation: `GO`
- Codex verification on repo clone: 41 targeted tests passed; broad non-live V2 suite 570 passed, 0 failed

### `QA-2026-05-19-006`

Status: `QA_GO`

Goal:

Review Dev output for page-post memory, admin sold-out/full override safety, source-context handling, and scope discipline.

Expected deliverables:

- `docs/tasks/QA_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Result:

- QA verdict: `GO`
- Migration 020 reviewed as additive/idempotent with RLS, CHECK constraints, and partial unique indexes.
- `page_post_context.py` reviewed as deterministic: no env reads, no live network, no LLM, no secrets, no wholesale names.
- QA verified targeted tests: 41 passed.
- QA verified broad non-live V2 suite: 563 passed, 7 skipped (flask-only), 0 failed.
- Next action: Codex review, commit/push QA artifacts, then apply migration 020 on staging before downstream wiring.

### `DEV-2026-05-19-007`

Status: `READY_FOR_QA`

Goal:

Wire the QA-cleared Page Post Intelligence foundation into the V2 sales-agent planning layer and deterministic admin command core.

Expected deliverables:

- Admin commands for recent posts and full/sold-out overrides.
- Response-planning wiring that blocks deterministic full/sold-out candidates.
- Compact page-post/ad/organic context passed to the response layer.
- V2-only code/tests/docs changes.
- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Operational note:

- Migration `20260519_020_page_post_intelligence.sql` has QA GO, but Codex could not apply it to staging yet because the Supabase connector requires re-authentication and local staging DB credentials are not present in the shell.
- Dev should not attempt live Supabase access; implement/test with local fakes only.

Result:

- Added/updated V2 admin command handling for page posts and availability overrides.
- Wired page-post planning into `write_response(..., planning=...)`.
- Wired `PlanningContext` construction into the orchestrator for non-silent turns.
- Added orchestrator-level tests for sold-out blocking, post-source blocking, compact context, and no-source fallback.
- Codex restored unrelated `deposit_confidence` propagation in `_get_tour_fees` to avoid a fee-memory regression.
- Codex verification on repo clone: targeted package tests `124 passed`; broad non-live V2 suite `608 passed`, `0 failed` after rerunning with a repo-local temp directory due Windows temp permission issue.

### `QA-2026-05-19-007`

Status: `QA_GO`

Goal:

Review Dev output for page-post/sold-out command wiring, response-planning blocking, compact context, and scope discipline.

Expected deliverables:

- `docs/tasks/QA_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Result:

- QA verdict: `GO`
- Reviewed DEV-2026-05-19-007 at commit `3bf63a7`.
- Verified admin command parsing, page-post source context, sold-out/full deterministic blocking, compact LLM planning note, and scope discipline.
- Verified blocked paths bypass the LLM and unblocked paths receive only compact `page_post_planning_note`.
- QA notes remaining operational risks: migration 020 still unapplied, production webhook source attribution not wired, LINE adapter staff allow-list still pending, and planner latency should be checked after migration.

Codex operational follow-up:

- Applied migration `20260519_020_page_post_intelligence.sql` to Supabase staging project `tourfiremai-v2-staging` (`mbcihtcdwfofagkxphcu`) via Supabase connector.
- Verified tables exist: `page_posts`, `page_post_tour_links`, `tour_availability_overrides`.
- Verified RLS is enabled on all three tables.
- Verified policies are service-role only plus explicit anon deny policies.
- Verified anon role sees `0` rows for all three tables.
- Remaining next-step risks: production webhook source attribution is not wired yet, LINE adapter staff allow-list is still pending, and planner latency should be checked once staging traffic exercises the new tables.

### `DEV-2026-05-19-008`

Status: `READY_FOR_QA`

Goal:

Implement Sprint 5 Package B as one integration package:

- Source attribution adapter for page post / ad / organic / unknown traffic.
- LINE admin allow-list adapter core before admin commands can run.
- Dashboard-safe read API/service v0 for current cases and post/tour status.
- Integration tests for blocked tours, source context, admin pause/resume, and safe admin reads.

Expected deliverables:

- V2-only code/tests.
- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Result:

- Dev verdict: `READY_FOR_QA`
- GitHub branch: `v2/s4-followup-vision-ondemand`
- Dev commit: `0803dce`
- Codex cleanup commit: `546efa5`
- Implemented V2-only source attribution adapter, LINE admin allow-list adapter, and dashboard-safe read API service.
- Targeted tests: `46 passed`
- Broad non-live V2 suite: `638 passed`
- No V1 / Make.com / production webhook / deploy / secrets / live provider changes.

Hard rules:

- No V1.
- No Make.com.
- No deploy.
- No production webhook changes.
- No secrets.
- No live paid providers.
- No Supabase migration apply from Claude Dev.

### `QA-2026-05-19-008`

Status: `QA_GO`

Goal:

Review DEV-2026-05-19-008 as one integration unit after Dev report is ready.

Expected deliverables:

- `docs/tasks/QA_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Verdict options:

- `GO`
- `GO_WITH_NOTES`
- `NO_GO`

Result:

- QA verdict: `GO`
- Reported by Tiw from Claude QA session.
- Codex note: QA report file was not pushed into this repo at the time of controller update, so this log records owner-reported QA status and the repository evidence from Dev/Codex verification.
- Codex verification before QA handoff: targeted tests `46 passed`, broad non-live V2 suite `638 passed`.

### `DEV-2026-05-19-009`

Status: `READY_FOR_QA`

Goal:

Implement Sprint 5 Package C as one runtime wiring package:

- Wire source attribution into the V2 webhook/runtime path or an explicitly safe source-record seam.
- Add a safe LINE admin runtime entrypoint around the existing allow-list adapter.
- Add a minimal guarded dashboard read HTTP/API shim over `AdminDashboardAPI`.
- Keep all work V2-only and staging-safe.

Expected deliverables:

- V2-only code/tests.
- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Result:

- Dev verdict: `READY_FOR_QA`
- GitHub branch: `v2/s4-followup-vision-ondemand`
- Dev commit: `08ed251`
- Implemented Sprint 5 Package C runtime wiring:
  - Meta webhook records deterministic `source_attribution` conversation events while preserving silent ingest.
  - Added `POST /admin/line` route with `LineAdminAdapter` allow-list gate and no live LINE sends.
  - Added guarded `GET /admin/dashboard/{cases,cases/<id>,posts,handoffs}` read API shim.
  - Admin responses defensively strip raw PSIDs.
- Codex verification: targeted runtime suite `77 passed`; broad non-live V2 suite `662 passed`.
- No V1 / Make.com / deploy / production webhook settings / secrets / live provider changes.

Hard rules:

- No V1.
- No Make.com.
- No deploy.
- No production webhook settings changes.
- No secrets.
- No live Meta / LINE / OpenAI / OCR / paid-provider calls.
- No Supabase migration apply from Claude Dev.

### `QA-2026-05-19-009`

Status: `QA_GO`

Goal:

Review DEV-2026-05-19-009 as one integration unit after Dev report is ready.

Expected deliverables:

- `docs/tasks/QA_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Verdict options:

- `GO`
- `GO_WITH_NOTES`
- `NO_GO`

Result:

- QA verdict: `GO`
- Reported by Tiw from Claude QA session.
- Codex note: QA report file was not pushed into this repo at the time of controller update, so this log records owner-reported QA status and the repository evidence from Dev/Codex verification.

### `DEV-2026-05-19-010`

Status: `PENDING`

Goal:

Implement Sprint 5 Package D as one admin-only real-chat readiness package:

- Runtime smoke tests for webhook, source attribution, LINE admin route, and dashboard read API.
- Admin-only test-mode gate so only allowlisted admin/test PSIDs can pass during real-chat testing.
- Runtime config validator that reports readiness without printing secrets.
- Source-attribution smoke evidence for page-post / organic / unknown traffic.
- Admin handoff / pause smoke evidence.
- Short operator runbook for Tiw/admin staff.

Expected deliverables:

- V2-only code/tests/docs.
- `docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md`
- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Hard rules:

- No V1.
- No Make.com.
- No deploy.
- No production webhook settings changes.
- No secrets.
- No live Meta / LINE / OpenAI / OCR / paid-provider calls.
- No Supabase migration apply from Claude Dev.

### `QA-2026-05-19-010`

Status: `PENDING`

Goal:

Review DEV-2026-05-19-010 as one integration readiness package and decide whether Tiw can proceed toward admin-only real-chat testing.

Expected deliverables:

- `docs/tasks/QA_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Verdict options:

- `GO`
- `GO_WITH_NOTES`
- `NO_GO`

Result:

- QA verdict: `GO`
- Reported by Tiw from Claude QA session.
- Codex note: the detailed Claude QA matrix was not pushed into this repo at the time of controller update, so `docs/tasks/QA_REPORT_CURRENT.md` records an owner-reported QA result only and avoids fabricating evidence.
- Dev package commits reviewed by owner-reported QA:
  - `d374ac3` - `feat: add admin-only real chat readiness gate`
  - `6ebe374` - `docs: mark dev 010 ready for qa`

Controller outcome:

- `DEV-2026-05-19-010` accepted.
- `QA-2026-05-19-010` accepted.
- Next stage: admin-only staging test preparation. V2 is still not approved for production customer-wide traffic.

### `DEV-2026-05-19-011`

Status: `PENDING`

Goal:

Implement Sprint 5 Package E as one admin-only staging preflight package:

- Add a V2-only local preflight entrypoint for required staging configuration.
- Verify admin-only mode, admin/test PSID allow-list, dashboard token, LINE admin allow-list, staging Supabase URL, staging Redis URL, Meta staging app secret, and verify token presence without printing values.
- Treat OpenAI/OCR/live provider keys as not required for admin-only preflight.
- Add a non-network migration 020 readiness check for page-post intelligence tables, RLS, anon-deny, and service_role policies.
- Update the admin-only real chat runbook with a "Before Meta Webhook Change" checklist.
- Add tests and run targeted + broad non-live V2 suite when feasible.

Expected deliverables:

- V2-only code/tests/docs.
- Updated `docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md`.
- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Hard rules:

- No V1.
- No Make.com.
- No deploy.
- No production webhook settings changes.
- No secrets.
- No live Meta / LINE / OpenAI / OCR / paid-provider calls.
- No Supabase migration apply from Claude Dev.
- No customer-facing outbound replies.

### `QA-2026-05-19-011`

Status: `PENDING`

Goal:

Review DEV-2026-05-19-011 as one integrated admin-only staging preflight package.

Expected deliverables:

- `docs/tasks/QA_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Verdict options:

- `GO`
- `GO_WITH_NOTES`
- `NO_GO`
- `BLOCKED`

Result:

- Dev status: `READY_FOR_QA`
- QA verdict: `GO_WITH_NOTES`
- Reports synced from Cowork workspace into the controller repo by Codex.
- Codex verification on local clone:
  - Targeted new suite: `51 passed`
  - Adjacency suite: `147 passed`
  - Broad non-live V2 suite: `798 passed / 4 skipped / 0 failed` using `--basetemp=.pytest_tmp -p no:cacheprovider`
- Note: an initial broad-suite run failed because Windows denied access to the default pytest temp directory (`AppData\Local\Temp\pytest-of-supak`), not because of code failures. Re-run with repo-local basetemp passed.
- Controller outcome: `DEV-2026-05-20-013` accepted, `QA-2026-05-20-013` accepted with notes.
- Next action: commit/push the DEV-013 code + QA artefacts, then open the next wiring task for orchestrator/response-writer integration and follow-up database/index cleanup.

Result:

- QA verdict: `GO`
- Reported by Tiw from Claude QA session.
- Codex note: detailed QA report was not pasted into this repo at the time of controller update, so this log records owner-reported QA status.
- Controller outcome: `DEV-2026-05-19-011` accepted, `QA-2026-05-19-011` accepted.

## 2026-05-20

### `DEV-2026-05-20-012`

Status: `PENDING`

Goal:

Implement Sprint 5 Package F as one detail-page departure price table parser package:

- Parse `tourfiremai.com/tour/<web_code>` detail pages.
- Extract per-departure rows with exact date range, adult price, child price, single supplement, joinland, group size, and status text.
- Keep `web_code`, `tour_code_real`, and airline distinct.
- Add additive migration 021 for detailed departure row fields.
- Add a read-only live smoke CLI if appropriate.

Expected deliverables:

- V2-only code/tests/docs.
- `v2/supabase/migrations/20260520_021_departure_price_rows.sql`
- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Hard rules:

- No V1.
- No Make.com.
- No deploy.
- No production webhook settings changes.
- No secrets.
- No live Meta / LINE / OpenAI / OCR / paid-provider calls.
- No Supabase migration apply from Claude Dev.
- No customer-facing outbound replies.

### `QA-2026-05-20-012`

Status: `GO_WITH_NOTES`

Goal:

Review DEV-2026-05-20-012 as one integrated detail-page departure price table parser package.

Expected deliverables:

- `docs/tasks/QA_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Verdict options:

- `GO`
- `GO_WITH_NOTES`
- `NO_GO`
- `BLOCKED`

Result:

- QA verdict: `GO_WITH_NOTES`
- Reported by Tiw from Claude QA session.
- Codex note: detailed QA matrix was not committed to this repo at the time of controller update, so this log records owner-reported QA status without fabricating note details.
- Controller outcome: `DEV-2026-05-20-012` accepted, `QA-2026-05-20-012` accepted with notes.
- Next action: apply migration `20260520_021_departure_price_rows.sql` to V2 Supabase staging, then open the follow-up Dev task to wire parsed departure rows into scraper/detail enrichment and selected-tour memory.

Controller follow-up:

- Codex applied migration `20260520_021_departure_price_rows.sql` to V2 Supabase staging project `tourfiremai-v2-staging` (`mbcihtcdwfofagkxphcu`).
- Verification passed: 15 new columns present, 4 constraints present, 3 indexes present.
- No V1 / production / Make.com / deployment changes.

### `DEV-2026-05-20-013`

Status: `PENDING`

Goal:

Wire the DEV-012 detail-page departure parser into V2 scraper/detail enrichment and selected-tour memory:

- use `/tour/<web_code>` detail reads only;
- persist parsed departure rows to migration 021 fields idempotently;
- preserve `web_code`, `tour_code_real`, and airline separately;
- make selected-date row matching deterministic and refuse to guess;
- keep customer-facing production behavior unchanged.

Expected deliverables:

- V2-only scraper/detail enrichment code.
- V2-only selected-tour row matching / memory helper code.
- Tests.
- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Hard rules:

- No V1.
- No Make.com.
- No deploy.
- No production webhook settings changes.
- No secrets.
- No live Meta / LINE / OpenAI / OCR / paid-provider calls.
- No customer-wide traffic.

### `QA-2026-05-20-013`

Status: `PENDING`

Goal:

Review DEV-2026-05-20-013 as one integrated detail departure rows wiring package.

Expected deliverables:

- `docs/tasks/QA_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Verdict options:

- `GO`
- `GO_WITH_NOTES`
- `NO_GO`
- `BLOCKED`

Result:

- QA verdict: `GO_WITH_NOTES`
- Controller outcome: `DEV-2026-05-20-013` accepted, `QA-2026-05-20-013` accepted with notes.
- Codex synced and committed the DEV-013 implementation and QA status:
  - `e3ebe3b` - `feat(v2): wire detail departure enrichment`
  - `4ef8114` - `docs(tasks): sync dev 013 accepted status`
- Codex verification on local clone:
  - Targeted new suite: `51 passed`
  - Adjacency suite: `147 passed`
  - Broad non-live V2 suite: `798 passed / 4 skipped / 0 failed` using `--basetemp=.pytest_tmp -p no:cacheprovider`
- Next action: open DEV/QA-014 to wire selected departure planning into the orchestrator and response writer.

### `DEV-2026-05-20-014`

Status: `PENDING`

Goal:

Wire selected departure detail planning into the V2 orchestrator and response writer:

- use selected-tour memory before replying;
- enrich detail only when selected-tour follow-up requires it;
- match customer date/pax text against parsed departure rows;
- pass exact high-confidence row data to response planning;
- ask precise confirmation instead of guessing when confidence is low;
- keep customer-facing production behavior unchanged.

Expected deliverables:

- V2-only orchestrator / response-planning code and tests.
- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Hard rules:

- No V1.
- No Make.com.
- No deploy.
- No production webhook settings changes.
- No secrets.
- No live Meta / LINE / OpenAI / OCR / paid-provider calls.
- No Supabase migration apply from Claude Dev.
- No customer-wide traffic.

### `QA-2026-05-20-014`

Status: `PENDING`

Goal:

Review DEV-2026-05-20-014 as one integrated selected departure planning package.

Expected deliverables:

- `docs/tasks/QA_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Verdict options:

- `GO`
- `GO_WITH_NOTES`
- `NO_GO`
- `BLOCKED`

Result:

- QA verdict: `GO_WITH_NOTES`
- Controller outcome: `DEV-2026-05-20-014` accepted, `QA-2026-05-20-014` accepted with notes.
- Codex synced and committed the DEV-014 implementation and QA status:
  - `a1be370` - `feat(v2): wire selected departure planning`
- Codex verification on local clone:
  - Targeted/adjoining selected-departure suite: `71 passed`
  - Broad non-live V2 suite: `830 passed / 4 skipped / 0 failed` with live OpenAI health excluded because this local environment had a staging key set but no live OpenAI package path for non-live testing.
- QA carry-over / next-package notes:
  - Add freshness TTL / scheduled refresh for stored departure rows.
  - Tighten departure-row uniqueness after backfill audit.
  - Fix listing scraper canonical URLs from `/intertourdetail/<code>` to `/tour/<web_code>`.
- Next action: open DEV/QA-015 as an integrated data-freshness and canonical-url hardening package before real-chat testing.



### `DEV-2026-05-20-015`

Status: `PENDING`

Goal:

Implement Sprint 5 Package I: departure-row freshness, canonical `/tour/<web_code>` URL fix, and uniqueness readiness before admin-only real-chat testing.

Expected deliverables:

- V2-only code/tests/migrations as needed.
- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Hard rules:

- No V1.
- No Make.com.
- No deploy.
- No production webhook settings changes.
- No secrets.
- No live Meta / LINE / OpenAI / OCR / paid-provider calls.
- No Supabase migration apply from Claude Dev.
- No customer-wide traffic.

### `QA-2026-05-20-015`

Status: `PENDING`

Goal:

Review DEV-2026-05-20-015 as one integrated data-freshness and canonical-url hardening package.

Expected deliverables:

- `docs/tasks/QA_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Verdict options:

- `GO`
- `GO_WITH_NOTES`
- `NO_GO`
- `BLOCKED`

Result:

- QA verdict: `GO_WITH_NOTES`
- Controller outcome: `DEV-2026-05-20-015` accepted, `QA-2026-05-20-015` accepted with notes.
- Codex synced and committed the DEV-015 implementation and QA status:
  - `85a4f65` - `feat(v2): add departure row freshness controls`
- Codex verification on local clone:
  - Targeted freshness/detail suite: `62 passed`
  - Broad non-live V2 suite: `850 passed / 4 skipped / 0 failed`
- QA verification:
  - Canonical URL now stores `/tour/<web_code>`.
  - Migration 022 is additive and NOT applied.
  - UNIQUE proposal remains `.sql.proposal`, not an executable migration.
  - Refresh failure fails closed and avoids fetch loops.
  - Refresher CLI is operator-driven and dry-run by default.
- QA notes:
  - Migration 022 still needs explicit controller/Tiw approval before applying to staging.
  - UNIQUE promotion remains gated behind duplicate audit.
  - Operator runbook for refresher still needed.
- Next action: Codex/Tiw decides whether to apply migration 022 to V2 staging, then open the next task for operator runbook / staging preflight.

### Controller Action — `MIGRATION-2026-05-20-022`

Status: `APPLIED_TO_STAGING`

Goal:

Apply `20260520_022_departure_refreshed_at.sql` to V2 Supabase staging after `QA-2026-05-20-015` returned `GO_WITH_NOTES` and Tiw explicitly approved staging application.

Result:

- Applied migration 022 to Supabase staging project `tourfiremai-v2-staging` (`mbcihtcdwfofagkxphcu`) via Supabase MCP `apply_migration`.
- Verified `public.tour_departures.refreshed_at` exists as nullable `timestamp with time zone`.
- Verified partial index `idx_dep_refreshed_at` exists on `tour_departures(refreshed_at) WHERE refreshed_at IS NOT NULL`.
- Verified staging backfill result: `24/24` `tour_departures` rows have `refreshed_at` populated.
- Did not apply `_pending_023_departure_unique.sql.proposal`.
- Did not touch V1, Make.com, production webhook, deployments, secrets, live paid providers, or customer-facing traffic.

Next action:

Open the next Controller task for the departure refresher operator runbook / staging preflight, then run duplicate audit before considering the UNIQUE proposal.

### Controller Action — `DUPLICATE-AUDIT-2026-05-20-023`

Status: `PASSED_ZERO_ROWS`

Goal:

Run the read-only duplicate audit for the future `tour_departures` logical UNIQUE key before any consideration of `_pending_023_departure_unique.sql.proposal`.

Result:

- Ran the duplicate audit on Supabase staging project `tourfiremai-v2-staging` (`mbcihtcdwfofagkxphcu`) using the same logical key proposed by `_pending_023_departure_unique.sql.proposal`:
  - `tour_id`
  - `departure_start`
  - `departure_end`
  - `COALESCE(bus, 0)`
- Result returned `0` duplicate groups.
- Did not apply `_pending_023_departure_unique.sql.proposal`.
- Did not mutate data.
- Did not touch V1, Make.com, production webhook, deployments, secrets, live paid providers, or customer-facing traffic.

Next action:

Keep UNIQUE promotion gated for a separate explicit approval/task. Continue with admin-only staging real-chat preflight.

### Controller Action — `ADMIN-ONLY-SMOKE-2026-05-20`

Status: `PASSED`

Goal:

Verify existing admin-only runtime smoke coverage before opening the real-chat preflight package.

Result:

- Ran `v2/tests/test_admin_only_runtime_smoke.py`: `9 passed`.
- Ran the targeted runtime group:
  - `v2/tests/test_webhook.py`
  - `v2/tests/test_webhook_source_attribution.py`
  - `v2/tests/test_line_admin_runtime.py`
  - `v2/tests/test_admin_dashboard_runtime.py`
  - `v2/tests/test_admin_only_runtime_smoke.py`
- Targeted runtime group result: `40 passed`.
- No live Meta / LINE / OpenAI / OCR / paid-provider calls.
- Did not touch V1, Make.com, production webhook, deployments, secrets, migrations, or customer-facing traffic.

Next action:

Open `DEV-2026-05-20-016` / `QA-2026-05-20-016` as the integrated admin-only staging real-chat preflight package.

### `DEV-2026-05-20-016`

Status: `READY_FOR_QA`

Goal:

Finalize the admin-only staging real-chat runbook and verify runtime safety so Tiw can test V2 with a real Messenger/admin chat without processing customer-wide traffic or enabling V2 customer outbound.

Expected deliverables:

- Updated `docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md`.
- V2-only code/tests if needed.
- `docs/tasks/DEV_REPORT_CURRENT.md`.
- `docs/tasks/AGENT_STATUS.json`.

Result:

- Finalized `docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md` for admin-only staging real-chat preflight.
- Added `v2/tools/signed_meta_webhook_smoke.py` for offline-safe signed Meta webhook smoke payload generation.
- Added `v2/tests/test_signed_meta_webhook_smoke.py`.
- Dev evidence:
  - New helper/admin-only smoke: `24 passed`.
  - Adjacency suite: `55 passed`.
  - Broad non-live V2 suite: `862 passed / 0 failed` in Claude Dev sandbox.
- No runtime V2 modules changed.
- No V1, Make.com, Cloudflare worker, production webhook, secrets, live providers, migration apply, customer-wide traffic, or V2 customer-facing outbound changes.

Hard rules:

- No V1.
- No Make.com.
- No deploy.
- No production webhook settings changes.
- No secrets.
- No live Meta / LINE / OpenAI / OCR / paid-provider calls.
- No Supabase migration apply from Claude Dev.
- No customer-wide traffic.
- No customer-facing V2 outbound replies.

### `QA-2026-05-20-016`

Status: `GO_WITH_NOTES`

Goal:

Review `DEV-2026-05-20-016` as one integrated admin-only real-chat preflight package.

Expected deliverables:

- `docs/tasks/QA_REPORT_CURRENT.md`.
- `docs/tasks/AGENT_STATUS.json`.

Verdict options:

- `GO`
- `GO_WITH_NOTES`
- `NO_GO`
- `BLOCKED`

Result:

- QA verdict: `GO_WITH_NOTES`.
- QA evidence:
  - Targeted DEV-016 helper + admin-only smoke: `24 passed`.
  - Adjacency suite: `55 passed`.
  - Broad non-live V2 suite: `862 passed / 0 skipped / 0 failed`.
- No P0/P1 findings.
- P2 notes:
  - Helper POST URL is protected by convention and operator warning, not a hard URL denylist.
  - `_pending_023_departure_unique.sql.proposal` remains gated and unapplied.
  - Scheduled refresher operator wrapper documentation remains a future polish item.
- This QA verdict does **not** approve production Meta webhook changes, customer-facing V2 outbound replies, live LLM/OCR/paid-provider calls, migration apply, or production go-live.

