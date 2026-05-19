# CURRENT QA TASK

## Task ID
QA-2026-05-20-012

## Title
QA Review - Sprint 5 Package F Detail Page Departure Price Table Parser

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

Decide whether the detail page departure price table parser is deterministic, safe, and accurate enough to become the source of truth for row-level departure prices before admin-only real-chat testing.

This QA task does not approve production go-live.

## Required Checks

Review DEV-2026-05-20-012 as one integrated package.

Verify:

1. Parser is V2-only and does not touch V1.
2. Parser uses `/tour/<web_code>` for detail pages and does not rely on `/intertourdetail/<web_code>`.
3. Parser does not call LLM, OpenAI, OCR, Meta, LINE, Supabase, or paid providers in unit tests.
4. `web_code`, `tour_code_real`, and airline remain distinct and are not mixed.
5. `-`, empty strings, and non-price placeholders map to `NULL` / `None`, never `0`.
6. Thai date ranges parse correctly for same-month, cross-month, and Buddhist Era year suffix cases.
7. Row-level adult price, child price, single supplement, joinland, group size, and status text are captured where present.
8. Contact/status text such as "ติดต่อเจ้าหน้า" is preserved but not interpreted as sold-out.
9. Migration 021 is additive and backward-compatible with existing `tour_departures` fields.
10. Adapter maps `adult_price` to legacy `price` without losing detailed row fields.
11. Read-only live smoke CLI, if added, does not write to DB, does not print secrets, and is safe to run manually.
12. Tests cover parser success, parser edge cases, migration SQL, and no-regression paths.
13. Broad non-live suite has no regressions, or Dev explains a credible environment limitation.

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

