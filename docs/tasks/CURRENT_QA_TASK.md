# CURRENT QA TASK

## Task ID
QA-2026-05-20-013

## Title
QA Review - Sprint 5 Package G Detail Departure Rows Wiring

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

If local workspace lacks git/source files, differs from the GitHub branch, or cannot inspect the changed files, stop and report:

`BLOCKED: source-of-truth repo unavailable`

Do not invent scope from chat memory.

## Trigger

Run this QA task only after Dev writes:

- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json` with `READY_FOR_QA`

## Review Goal

Decide whether DEV-2026-05-20-013 safely wires the detail departure parser into scraper/detail enrichment and selected-tour memory without changing production behavior.

This QA task does not approve production go-live.

## Required Checks

Review DEV-2026-05-20-013 as one integrated package.

Verify:

1. DEV-012 parser is reused and not reimplemented inconsistently.
2. Detail reads use `/tour/<web_code>` only, not `/intertourdetail/<web_code>`.
3. Row persistence/mapping is idempotent and non-destructive.
4. `-`, empty strings, and non-price placeholders remain `NULL` / `None`, never `0`.
5. `web_code`, `tour_code_real`, and airline remain distinct.
6. Selected-date matching is deterministic and refuses to guess on ambiguous/no-match input.
7. Contact/status text is preserved but not interpreted as sold-out.
8. No unit tests call live network, LLM, OpenAI, OCR, Meta, LINE, or paid providers.
9. Broad non-live suite has no regressions, or Dev explains a credible environment limitation.
10. No V1, Make.com, production webhook, deploy, or secret changes.

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

