# Current QA Task

Task ID: `QA-2026-05-19-007`
Status: `PENDING`
Assigned role: Claude Cowork QA
Controller: Codex

## Task

Review Dev task `DEV-2026-05-19-007`.

This QA task is read-only. Do not patch code unless Codex explicitly asks for a QA patch.

## Context

`DEV-2026-05-19-007` should wire the QA-cleared Page Post Intelligence foundation into the V2 planning/admin-command layer.

The business problem:

- Admin posts tours on Facebook daily.
- Customers may chat from page posts, ads, or organic inbox.
- Admin needs a deterministic way to mark posted tours or tour codes as `full` / `sold_out`.
- The bot must not recommend a tour that deterministic code says is full/sold out.

Migration `20260519_020_page_post_intelligence.sql` has QA GO, but may not yet be applied to staging at the time of this QA. This QA reviews code and tests only; do not attempt live Supabase access.

## Review Scope

Read:

1. `docs/AI_COMMAND_CENTER.md`
2. `docs/V2_PAGE_POST_INTELLIGENCE_PLAN.md`
3. `docs/tasks/CURRENT_DEV_TASK.md`
4. `docs/tasks/CURRENT_QA_TASK.md`
5. `docs/tasks/TASK_LOG.md`
6. `docs/tasks/DEV_REPORT_CURRENT.md`
7. `docs/tasks/AGENT_STATUS.json`
8. Dev-changed V2 files

## QA Checks

Verify:

1. Dev stayed on V2 scope only.
2. V1 production code was not changed.
3. Make.com / Cloudflare / Meta production webhook behavior was not changed.
4. No secrets were written to files.
5. No live Meta/Facebook, LINE, OpenAI, OCR, Supabase production, or paid-provider calls are required by tests.
6. Admin command parsing is conservative and deterministic.
7. `posts` output is compact and does not dump full captions.
8. `mark_full` / `mark_sold_out` can set the deterministic override.
9. `clear_full` / `clear_sold_out` can clear the deterministic override.
10. Ambiguous admin command targets ask for clarification instead of guessing.
11. Response planning blocks a tour marked `full` or `sold_out`.
12. Response planning blocks a candidate tied to a full/sold-out page post when source context is present.
13. Response planning allows candidates when no active override exists.
14. The response writer does not recommend blocked tours.
15. LLM does not decide sold-out/full semantics.
16. LLM context is compact and does not include full page-post history.
17. New admin/bot text contains no wholesale partner names and no secrets.
18. Tests cover all required behaviors.
19. Broad non-live V2 suite passes or any skips/failures are clearly justified.

## Deliverable

Write:

`docs/tasks/QA_REPORT_CURRENT.md`

Update:

`docs/tasks/AGENT_STATUS.json`

Do not commit or push from Claude Cowork if the workspace has no `.git` checkout.
Codex will commit and push the QA report/status after reading the files from the shared workspace.

## QA Report Format

Include:

1. Verdict: `GO`, `GO_WITH_NOTES`, or `NO_GO`
2. Scope reviewed
3. Evidence checked
4. Findings by severity
5. Tests verified
6. Remaining risks
7. Next recommended step

## Stop Condition

After writing the report/status, stop. Do not continue implementation.
