# CURRENT DEV TASK

## Task ID
DEV-2026-05-19-010

## Title
Sprint 5 Package D — Admin-Only Real Chat Readiness, Runtime Smoke Tests, and Operator Runbook

## Status
PENDING

## Assigned Role
Claude Dev

## Controller
Codex

## Source of Truth

Use GitHub repo as source of truth:

- Repo: `github.com/tiwsoonkyu/tourfiremai-bot`
- Branch: `v2/s4-followup-vision-ondemand`

If your local Cowork workspace lacks git/source files, differs from the GitHub branch, or cannot inspect the changed files, stop and report:

`BLOCKED: source-of-truth repo unavailable`

Do not invent scope from chat memory. This file is the approved scope.

## Context

Sprint 5 Package C is controller-accepted and QA-cleared:

- Source attribution is wired into the V2 webhook as a safe source-record seam.
- `POST /admin/line` exists behind a LINE admin allow-list adapter and does not call live LINE send.
- Guarded dashboard read API routes exist for cases, posts, and handoffs.
- V2 webhook still preserves silent ingest; live customer replies are not enabled yet.

Tiw's next business goal is to reach safe testing with a real admin chat before customer-wide go-live.

The system must prove that:

1. Admin-only testing can be enabled without affecting normal customers.
2. Bot/customer outbound behavior cannot accidentally activate.
3. Admin pause/handoff controls are observable and testable.
4. Source context from page posts / ads / organic traffic is captured for future response planning.
5. Operators have a short runbook for how to test and what to watch.

## Goal

Prepare V2 for an admin-only real-chat test by adding runtime smoke coverage, configuration validation, safety gates, and an operator runbook.

This task is not a production launch. It is a readiness package.

## Scope

Implement all subtasks below as one integrated Dev package. Do not stop after each small subtask unless a P0 risk appears.

### 1. Runtime Smoke Test Harness

Add a lightweight smoke-test entrypoint or test module that exercises V2 runtime surfaces without live external calls.

It must cover:

- V2 webhook health path if present.
- Meta webhook verification path if present.
- Meta message ingest path with page-post source payload.
- Meta message ingest path with organic/unknown source payload.
- `POST /admin/line` unauthorized sender.
- `POST /admin/line` authorized sender.
- Dashboard API unauthorized request.
- Dashboard API authorized request.

The smoke harness must not call:

- Meta Graph API
- LINE API
- OpenAI
- OCR/document providers
- production webhook endpoints

### 2. Admin-Only Real Chat Gate Specification

Add a deterministic config validation layer or documented runtime guard for future admin-only testing.

Required behavior:

- If `V2_ADMIN_ONLY_TEST_MODE=true`, only allow inbound messages from configured admin/test PSIDs.
- Non-allowlisted customer messages must be safely ignored or recorded only, with no outbound reply.
- Missing allow-list while admin-only mode is enabled must fail closed.
- The rule must be test-covered, even if it is implemented as a small helper before full webhook deployment.

Acceptable implementation:

- A helper module such as `v2/webhook/test_mode_gate.py`, or
- A narrow function in `v2/webhook/app.py`, if simpler and well tested.

Do not enable live outbound replies in this task.

### 3. Runtime Configuration Validator

Add a small validator that can report whether staging runtime config is ready for admin-only test.

It should check presence/shape only, never print secret values.

Suggested checks:

- dashboard admin token present
- LINE admin allow-list present
- admin-only test mode status
- admin/test PSID allow-list present when admin-only mode is true
- Supabase staging URL/config presence if already required by runtime

Output should be safe for logs and runbooks:

- `ok`
- `missing`
- `configured`
- `disabled`

Never echo tokens, keys, raw secrets, or full PSIDs.

### 4. Source Attribution Smoke Evidence

Add tests proving that a page-post source payload produces a source attribution event or a source attribution object through the runtime seam.

Also prove:

- user text cannot spoof post/ad IDs
- unknown source preserves old behavior
- page-post source can be carried toward orchestrator planning kwargs or source-record seam

### 5. Admin Handoff / Pause Smoke Evidence

Add tests or smoke coverage proving:

- unauthorized admin command causes no state mutation
- authorized pause command reaches the admin command core
- authorized resume command reaches the admin command core
- admin command output is safe and does not expose raw PSID when a display name/masked id is available

Do not mark paid. Do not confirm booking. Do not send live LINE messages.

### 6. Operator Runbook

Create:

- `docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md`

The runbook must be short and practical for Tiw/admin staff.

Include:

1. Purpose of admin-only test.
2. Required environment variables, names only, no values.
3. How to run local/staging smoke tests.
4. How to enable admin-only mode safely.
5. How to test with Tiw/admin PSID.
6. What must be watched during the first 30 minutes.
7. Pause criteria.
8. Rollback/disable steps.
9. What is still not live yet.

### 7. Task Reports

Write:

- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

Then stop for QA.

## Allowed Files

Prefer touching only:

- `v2/webhook/app.py`
- `v2/webhook/*.py`
- `v2/lib/source_attribution.py` only if a runtime bug is found
- `v2/lib/line_admin_adapter.py` only if a runtime bug is found
- `v2/lib/admin_dashboard_api.py` only if a runtime bug is found
- `v2/tests/test_*smoke*.py`
- `v2/tests/test_webhook*.py`
- `v2/tests/test_line_admin_runtime*.py`
- `v2/tests/test_admin_dashboard_runtime*.py`
- `docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md`
- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

If you need to touch any file outside this list, explain why in the Dev report.

## Out of Scope

- Do not touch V1 production files.
- Do not touch Make.com.
- Do not change production webhook settings.
- Do not deploy.
- Do not call Meta Graph API.
- Do not call live LINE API.
- Do not call OpenAI / OCR / paid providers.
- Do not apply Supabase migrations.
- Do not build dashboard frontend UI.
- Do not enable live customer replies.
- Do not rotate or print secrets.
- Do not mark bookings as paid.
- Do not confirm seats or final price.

## Required Tests

Run at minimum:

```bash
pytest v2/tests/test_webhook.py v2/tests/test_webhook_source_attribution.py v2/tests/test_line_admin_runtime.py v2/tests/test_admin_dashboard_runtime.py -q
pytest v2/tests --ignore=v2/tests/test_integration_staging.py --ignore=v2/tests/test_live_openai_health.py --ignore=v2/tests/test_phase2_live_followup.py -p no:cacheprovider -q
```

If Windows temp permission blocks pytest, rerun with a repo-local `--basetemp`.

## Required Dev Report

Write `docs/tasks/DEV_REPORT_CURRENT.md` with:

1. Status
2. Scope implemented
3. Files changed
4. Admin-only safety gate design
5. Runtime smoke coverage
6. Config validator behavior
7. Tests run and results
8. Safety checks
9. Known gaps / next recommended action

Then update `docs/tasks/AGENT_STATUS.json` with:

- `status`: `READY_FOR_QA` or `BLOCKED`
- `current_dev_task`: `DEV-2026-05-19-010`
- `next_action`: `CLAUDE_QA_RUN_CURRENT_QA_TASK` if ready

Stop after writing the report.
