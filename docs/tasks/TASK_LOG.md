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

Status: `PENDING`

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

### `QA-2026-05-19-007`

Status: `PENDING`

Goal:

Review Dev output for page-post/sold-out command wiring, response-planning blocking, compact context, and scope discipline.

Expected deliverables:

- `docs/tasks/QA_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`
