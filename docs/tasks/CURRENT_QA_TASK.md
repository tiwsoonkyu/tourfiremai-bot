# CURRENT QA TASK

## Task ID
QA-2026-05-19-011

## Title
QA Review - Sprint 5 Package E Admin-Only Staging Preflight

## Status
PENDING

## Assigned Role
Claude QA

## Controller
Codex

## Source of Truth

Use GitHub repo as source of truth:

- Repo: `github.com/tiwsoonkyu/tourfiremai-bot`
- Branch: `v2/s4-followup-vision-ondemand`

If your local Cowork workspace lacks git/source files, differs from the GitHub branch, or cannot inspect the changed files, stop and report:

`BLOCKED: source-of-truth repo unavailable`

Do not invent scope from chat memory.

## Trigger

Run this QA task only after Dev writes:

- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json` with `READY_FOR_QA`

## Review Goal

Decide whether the admin-only staging preflight package is safe and clear enough for Codex/Tiw to proceed toward manual staging setup and admin-only Messenger testing.

This QA task does not approve production go-live.

## Required Checks

Review DEV-2026-05-19-011 as one integrated package.

Verify:

1. New preflight code is V2-only and does not touch V1.
2. Preflight never prints raw secrets, raw PSIDs, service keys, passwords, app secrets, webhook tokens, or OpenAI keys.
3. Preflight reports admin-only mode disabled as not ready.
4. Preflight reports missing required staging env vars clearly.
5. Preflight treats OpenAI/OCR/live provider keys as not required for admin-only preflight.
6. Migration 020 readiness check verifies the three expected tables.
7. Migration 020 readiness check verifies RLS, anon-deny, and service_role policies.
8. Dev did not apply Supabase migrations.
9. Dev did not call Meta, LINE, OpenAI, OCR, paid providers, or production webhooks.
10. Runbook now has a clear "Before Meta Webhook Change" checklist.
11. Tests cover both ready and not-ready paths.
12. Broad non-live suite has no regressions, or Dev explains a credible environment limitation.

## Out of Scope For QA

QA must not:

- Fix source code.
- Apply migrations.
- Deploy anything.
- Touch V1.
- Touch Make.com.
- Touch secrets.
- Run live paid-provider calls.
- Approve production customer-wide go-live.

## Expected Deliverables

- `docs/tasks/QA_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

## Verdict Options

- `GO`
- `GO_WITH_NOTES`
- `NO_GO`
- `BLOCKED`

## Required QA Report

Write `docs/tasks/QA_REPORT_CURRENT.md` with:

1. Verdict
2. Scope reviewed
3. Evidence checked
4. Findings by severity
5. Tests verified
6. Remaining risks
7. Next recommended step

Then stop for Codex.
