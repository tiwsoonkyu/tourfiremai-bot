# DEV-2026-05-20-016 — Sprint 5 Package J

## Title

Admin-only staging real-chat preflight and operator runbook finalization.

## Status

`PENDING`

## Assigned Role

Claude Dev

## Controller

Codex

## Branch

`v2/s4-followup-vision-ondemand`

## Background

Tiw wants to test V2 with a real Messenger/admin chat as soon as possible. The previous package (`DEV/QA-2026-05-20-015`) returned `GO_WITH_NOTES`. Codex has already applied migration 022 to V2 Supabase staging and verified:

- `tour_departures.refreshed_at` exists.
- `idx_dep_refreshed_at` exists.
- `24/24` staging departure rows have `refreshed_at`.
- The duplicate audit for the future UNIQUE key returned zero rows.
- Admin-only smoke tests passed locally:
  - `v2/tests/test_admin_only_runtime_smoke.py`: 9 passed.
  - Webhook/source/LINE/dashboard runtime group: 40 passed.

The remaining business need is a safe, executable preflight package for admin-only real Messenger testing. This package must make it hard to accidentally process real customers or send customer-facing replies.

## Business Goal

Prepare V2 staging for a controlled real-chat test with Tiw/admin only:

- only allowlisted admin/test PSIDs may be processed;
- non-allowlisted Messenger traffic must be filtered;
- V2 must remain silent/no customer outbound unless a future task explicitly enables response delivery;
- admin operators must have a clear runbook, watch checklist, and rollback path.

## Scope

Implement this as one integrated preflight package. Do not stop after each small subtask unless a P0 risk appears.

### 1. Finalize Admin-Only Real Chat Runbook

Update `docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md` so an operator can execute it without guessing.

Required sections:

1. Staging-only warning.
2. Required env vars with safe descriptions, no values.
3. Runtime-config check and expected safe statuses.
4. Exact local smoke commands.
5. Exact signed Meta webhook local/staging smoke procedure, if the codebase already has a safe helper; otherwise document the missing helper as a Dev note.
6. Staging Meta webhook verification steps.
7. Admin PSID allowlist setup and verification.
8. Non-allowlisted PSID negative test.
9. First 30-minute watch checklist.
10. Immediate rollback / disable steps.
11. Explicit "not approved yet" list.

### 2. Verify / Harden Admin-Only Runtime Safety

Inspect existing V2 code paths for:

- webhook admin-only gate;
- runtime-config admin endpoint;
- source attribution adapter;
- LINE admin allowlist adapter;
- dashboard-safe admin read API.

If a small code or test patch is needed to make preflight safer, it may be added under `v2/` only. Keep changes minimal and deterministic.

Required safety invariants:

- Non-allowlisted PSIDs are filtered before customer/conversation state mutation.
- No V2 customer outbound response is sent by this package.
- Admin runtime-config output never includes raw secrets or raw PSIDs.
- Dashboard-safe read APIs mask PSIDs.
- LINE/admin command mutation remains allowlist-gated.
- Source attribution never trusts user-typed post IDs.

### 3. Record Staging Data Readiness

Add the following controller-verified facts to the runbook or Dev report:

- Migration 022 applied to staging.
- Duplicate audit returned zero rows.
- `_pending_023_departure_unique.sql.proposal` is not applied.
- Applying the UNIQUE proposal is still out of scope for this task.

### 4. Tests

Run targeted tests at minimum:

```bash
pytest v2/tests/test_admin_only_runtime_smoke.py -q
pytest \
  v2/tests/test_webhook.py \
  v2/tests/test_webhook_source_attribution.py \
  v2/tests/test_line_admin_runtime.py \
  v2/tests/test_admin_dashboard_runtime.py \
  v2/tests/test_admin_only_runtime_smoke.py \
  -q
```

Run the broad non-live V2 suite if feasible:

```bash
pytest v2/tests \
  --ignore=v2/tests/test_integration_staging.py \
  --ignore=v2/tests/test_live_openai_health.py \
  --ignore=v2/tests/test_phase2_live_followup.py \
  -q
```

If broad suite is blocked by local environment only, document the exact reason and the targeted pass results.

## Out of Scope

- Do not touch V1.
- Do not touch Make.com.
- Do not deploy.
- Do not modify production Meta webhook settings.
- Do not enable customer-wide traffic.
- Do not enable customer-facing V2 outbound replies.
- Do not apply Supabase migrations.
- Do not apply `_pending_023_departure_unique.sql.proposal`.
- Do not run live Meta / LINE / OpenAI / OCR / paid-provider calls.
- Do not write or expose secrets.
- Do not build a full dashboard UI in this task.

## Deliverables

- Updated `docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md`.
- V2-only tests/code if needed.
- `docs/tasks/DEV_REPORT_CURRENT.md`.
- `docs/tasks/AGENT_STATUS.json`.

## Required Dev Report

Write `docs/tasks/DEV_REPORT_CURRENT.md` with:

1. Summary.
2. Files changed.
3. Runbook changes.
4. Runtime safety verification.
5. Staging readiness facts.
6. Test results.
7. Safety / scope guard verification.
8. Known notes / risks.
9. Exact QA focus areas.
10. Recommendation: `GO`, `GO_WITH_NOTES`, or `NO_GO`.

Update `docs/tasks/AGENT_STATUS.json` to:

- `status`: `READY_FOR_QA`
- `current_dev_task`: `DEV-2026-05-20-016`
- `current_qa_task`: `QA-2026-05-20-016`
- `next_action`: `CLAUDE_QA_RUN_CURRENT_QA_TASK`

Then stop.
