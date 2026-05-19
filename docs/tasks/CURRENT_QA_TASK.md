# Current QA Task

Task ID: `QA-2026-05-19-005`
Status: `PENDING`
Assigned role: Claude Cowork QA
Controller: Codex

## Task

Review Dev task `DEV-2026-05-19-005`.

This QA task is read-only. Do not patch code unless Codex explicitly asks for a QA patch.

## Context

`DEV-2026-05-19-004` added a QA-cleared `admin_ops` foundation for pause/resume/case summary.

`DEV-2026-05-19-005` should add a deterministic LINE admin command handler core that wraps `admin_ops` without making live LINE API calls.

The most important product invariant remains:

> When a human/admin is handling a customer, the bot must not interrupt.

## Review Scope

Read:

1. `docs/AI_COMMAND_CENTER.md`
2. `docs/tasks/CURRENT_DEV_TASK.md`
3. `docs/tasks/CURRENT_QA_TASK.md`
4. `docs/tasks/TASK_LOG.md`
5. `docs/tasks/DEV_REPORT_CURRENT.md`
6. `docs/tasks/AGENT_STATUS.json`
7. Dev-changed V2 files

## QA Checks

Verify:

1. Dev stayed on V2 scope only.
2. V1 production code was not changed.
3. Make.com / Cloudflare / Meta production webhook behavior was not changed.
4. No secrets were written to files.
5. No live LINE, OpenAI, or paid-provider calls are required by tests.
6. No PDF extraction behavior or fee thresholds were changed.
7. Parser recognizes all required commands:
   - `cases`
   - `cases paused`
   - `handoffs`
   - `case <id>`
   - `pause <id>`
   - `resume <id>`
   - `help`
8. Unknown/ambiguous commands return safe help and do not mutate state.
9. `pause <id>` uses `admin_ops.pause_bot_for_customer(...)`.
10. `resume <id>` uses `admin_ops.resume_bot_for_customer(...)`.
11. `case <id>` uses `admin_ops.get_admin_case(...)`.
12. `cases` / `handoffs` use the admin_ops listing functions.
13. Admin output is safe for a staff LINE group:
    - no secrets
    - no wholesale partner names
    - PSIDs masked where appropriate
    - no customer-facing auto-reply text
14. Tests cover command parsing, listing, case detail, pause, resume, missing target, and leakage safety.
15. Broad non-live V2 suite passes or any skips/failures are clearly justified.

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
