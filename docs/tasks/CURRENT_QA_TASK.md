# QA-2026-05-20-014 — Sprint 5 Package H Review

## Title

Review selected departure detail planning integration.

## Status

`PENDING`

## Assigned Role

Claude QA

## Controller

Codex

## Branch

`v2/s4-followup-vision-ondemand`

## Depends On

`DEV-2026-05-20-014`

## Review Goal

Verify that Dev safely wired detail enrichment and selected departure matching into the V2 orchestrator/response path without changing production behavior or inventing tour facts.

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

### B. Orchestrator Behavior

6. Generic greeting / broad country discovery does not fetch detail pages.
7. Selected-tour follow-up uses the selected tour from memory instead of resetting the conversation.
8. Detail enrichment is called only when the customer asks for details, date, price, fee, tip, deposit, visa, single supplement, booking summary, or similar selected-tour follow-up.
9. Repeated messages do not trigger unbounded repeated detail-page fetches.

### C. Departure Matching

10. High-confidence selected date/pax row is passed to response planning.
11. Ambiguous/low-confidence date asks for confirmation and does not guess.
12. No matching date asks the customer to choose from available dates.
13. Past dates are not treated as valid matches.
14. Missing values and `-` remain `None`, never `0`.

### D. Data Correctness

15. `web_code`, `tour_code_real`, and airline remain separate.
16. Contact-button/status text is not treated as sold-out or seat availability.
17. Availability override logic still blocks full/sold-out candidates before LLM response.
18. Fee/tip/deposit/single supplement answers still follow the fee policy and handoff when confidence is low or data is missing.
19. No wholesale partner names appear in new runtime files or customer-facing response fixtures.

### E. Tests

20. Required targeted tests pass.
21. Broad non-live V2 suite passes, or Dev clearly documents an environment-only failure and reruns with repo-local `--basetemp`.

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
- `current_dev_task`: `DEV-2026-05-20-014`
- `current_qa_task`: `QA-2026-05-20-014`
- `next_action`: `WAITING_FOR_CODEX`

Then stop.
