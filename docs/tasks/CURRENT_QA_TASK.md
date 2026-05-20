# QA-2026-05-20-016 — Sprint 5 Package J Review

## Title

Review admin-only staging real-chat preflight and operator runbook finalization.

## Status

`PENDING`

## Assigned Role

Claude QA

## Controller

Codex

## Branch

`v2/s4-followup-vision-ondemand`

## Depends On

`DEV-2026-05-20-016`

## Review Goal

Verify that Dev made V2 staging ready for a safe admin-only real Messenger test without enabling customer-wide processing or outbound customer replies.

## Required Reading

Read:

1. `docs/AI_COMMAND_CENTER.md`
2. `docs/tasks/CURRENT_DEV_TASK.md`
3. `docs/tasks/CURRENT_QA_TASK.md`
4. `docs/tasks/TASK_LOG.md`
5. `docs/tasks/DEV_REPORT_CURRENT.md`
6. `docs/tasks/AGENT_STATUS.json`
7. `docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md`

Then inspect the relevant diff and tests.

## Required Checks

### A. Scope Discipline

1. No V1 files touched.
2. No Make.com / Cloudflare / production Meta webhook settings touched.
3. No secrets added, printed, or committed.
4. No live LINE / Meta / OpenAI / OCR / paid-provider calls in tests.
5. No Supabase migration applied from Dev.
6. No customer-wide traffic enabled.
7. No customer-facing V2 outbound reply enabled.

### B. Admin-Only Runtime Safety

8. Non-allowlisted PSIDs are filtered before customer/conversation state mutation.
9. Allowlisted admin/test PSIDs can pass the gate.
10. Runtime-config endpoint reports only safe configured/missing statuses.
11. Runtime-config endpoint never returns raw secrets or raw PSIDs.
12. Dashboard-safe read APIs mask PSIDs.
13. LINE/admin command mutation is allowlist-gated.
14. Source attribution does not trust user-typed post IDs.

### C. Runbook Quality

15. Runbook is executable by an operator without guessing.
16. Runbook includes env checklist, runtime check, smoke tests, admin PSID allowlist setup, non-allowlisted negative test, first 30-minute watch, and rollback.
17. Runbook states that V2 is not approved for production webhook or customer outbound.
18. Runbook records migration 022 applied and duplicate audit zero rows.
19. Runbook states UNIQUE proposal is not applied.

### D. Tests

20. Required targeted tests pass.
21. Broad non-live V2 suite passes, or Dev documents a legitimate environment-only reason with enough targeted evidence.

## Verdict Options

Use one:

- `GO`
- `GO_WITH_NOTES`
- `NO_GO`
- `BLOCKED`

## Required QA Report

Write `docs/tasks/QA_REPORT_CURRENT.md` with:

1. Verdict.
2. Scope reviewed.
3. Test results.
4. Findings, ordered by severity.
5. Required fixes, if any.
6. Notes / residual risks.
7. Recommendation to Codex.

Update `docs/tasks/AGENT_STATUS.json` with:

- `status`: `QA_GO`, `QA_GO_WITH_NOTES`, `QA_NO_GO`, or `QA_BLOCKED`
- `current_dev_task`: `DEV-2026-05-20-016`
- `current_qa_task`: `QA-2026-05-20-016`
- `next_action`: `WAITING_FOR_CODEX`

Then stop.
