# QA-2026-05-20-015 — Sprint 5 Package I Review

## Title

Review departure-row freshness, canonical tour URL fix, and uniqueness readiness.

## Status

`PENDING`

## Assigned Role

Claude QA

## Controller

Codex

## Branch

`v2/s4-followup-vision-ondemand`

## Depends On

`DEV-2026-05-20-015`

## Review Goal

Verify that Dev safely improved V2 data freshness and URL correctness without changing production behavior, applying migrations, or inventing tour facts.

## Required Reading

Read:

1. `docs/AI_COMMAND_CENTER.md`
2. `docs/tasks/CURRENT_DEV_TASK.md`
3. `docs/tasks/CURRENT_QA_TASK.md`
4. `docs/tasks/TASK_LOG.md`
5. `docs/tasks/DEV_REPORT_CURRENT.md`
6. `docs/tasks/AGENT_STATUS.json`

Then inspect the relevant diff and tests.

## Required Checks

### A. Scope Discipline

1. No V1 files touched.
2. No Make.com / Cloudflare / production Meta webhook settings touched.
3. No secrets added or printed.
4. No live LINE / Meta / OpenAI / OCR / paid-provider calls in tests.
5. No Supabase migration applied from Dev.
6. No customer-wide outbound behavior enabled.

### B. Canonical URL Correctness

7. `tours_canonical.url` generation uses `/tour/<web_code>`.
8. No V2 canonical tour URL path uses `/intertourdetail/` after this patch.
9. `web_code`, `tour_code_real`, and airline remain separate.

### C. Freshness / Refresh Behavior

10. Departure rows have a clear freshness field or equivalent policy.
11. Fresh rows do not trigger unnecessary HTTP detail fetches.
12. Stale rows trigger a bounded refresh path.
13. Refresh failure fails closed and does not quote final price/seat availability.
14. No unbounded repeated fetch loop is introduced.
15. Dry-run refresher does not write to DB.

### D. Uniqueness Readiness

16. Duplicate-audit query/helper uses the intended logical key.
17. Any proposed uniqueness migration is gated/safe and not applied by Dev.
18. No destructive data cleanup or row deletion is performed.

### E. Tests

19. Required targeted tests pass.
20. Broad non-live V2 suite passes, or Dev clearly documents an environment-only failure and reruns with safe local tmp settings.

## Verdict Options

Use one:

- `GO`
- `GO_WITH_NOTES`
- `NO_GO`
- `BLOCKED`

## Required QA Report

Write `docs/tasks/QA_REPORT_CURRENT.md` with:

1. Verdict
2. Scope reviewed
3. Test results
4. Findings, ordered by severity
5. Required fixes, if any
6. Notes / residual risks
7. Recommendation to Codex

Update `docs/tasks/AGENT_STATUS.json` with:

- `status`: `QA_GO`, `QA_GO_WITH_NOTES`, `QA_NO_GO`, or `QA_BLOCKED`
- `current_dev_task`: `DEV-2026-05-20-015`
- `current_qa_task`: `QA-2026-05-20-015`
- `next_action`: `WAITING_FOR_CODEX`

Then stop.
