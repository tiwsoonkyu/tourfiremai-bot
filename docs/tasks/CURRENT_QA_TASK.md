# CURRENT QA TASK

## Task ID
QA-2026-05-19-010

## Title
QA Review — Sprint 5 Package D Admin-Only Real Chat Readiness

## Status
PENDING

## Assigned Role
Claude QA

## Controller
Codex

## Dev Task Under Review
DEV-2026-05-19-010

## Source of Truth

Use GitHub branch `v2/s4-followup-vision-ondemand` in `github.com/tiwsoonkyu/tourfiremai-bot` as source of truth.

If local Cowork workspace files differ, required source files are missing, or the GitHub branch cannot be inspected, stop and report:

`BLOCKED: source-of-truth repo unavailable`

Do not invent scope from chat memory.

## Required Reading

Read:

1. `docs/AI_COMMAND_CENTER.md`
2. `docs/tasks/CURRENT_QA_TASK.md`
3. `docs/tasks/TASK_LOG.md`
4. `docs/tasks/DEV_REPORT_CURRENT.md`
5. `docs/tasks/AGENT_STATUS.json`
6. All files changed by DEV-2026-05-19-010

## QA Goal

Review DEV-2026-05-19-010 as one integration readiness package.

The question is not "is production ready?".

The question is:

Can Tiw safely proceed toward an admin-only real-chat test without exposing normal customers, secrets, V1 production, Make.com, production webhook settings, or live paid providers?

## Required Checks

### 1. Scope Discipline

Verify:

- V2 only.
- No V1.
- No Make.com.
- No production webhook setting change.
- No deployment.
- No migration apply.
- No secrets printed/written.
- No live Meta / LINE / OpenAI / OCR / paid-provider calls.

### 2. Runtime Smoke Coverage

Verify tests or smoke harness cover:

- V2 webhook health or equivalent.
- Meta webhook verification path if present.
- Meta message ingest with page-post source.
- Meta message ingest with organic/unknown source.
- Unauthorized `/admin/line`.
- Authorized `/admin/line`.
- Unauthorized dashboard API read.
- Authorized dashboard API read.

### 3. Admin-Only Test Gate

Verify:

- Admin-only mode exists as a deterministic guard or clearly documented helper.
- If admin-only mode is enabled, only configured admin/test PSIDs can pass.
- Missing allow-list while admin-only mode is enabled fails closed.
- Non-allowlisted messages do not produce outbound customer replies.
- Full raw PSIDs are not exposed in normal logs/reports.

### 4. Source Attribution Safety

Verify:

- Page-post source can be carried through runtime seam.
- Unknown/absent source preserves old behavior.
- User text cannot spoof post/ad IDs.
- No live Graph API calls.

### 5. LINE Admin Safety

Verify:

- Allow-list gate executes before command parsing/execution.
- Unauthorized sender causes no state mutation and no sensitive data leak.
- Authorized sender can reach supported commands.
- No live LINE send happens.

### 6. Dashboard Read Safety

Verify:

- Explicit auth/admin guard required.
- Payloads are compact.
- Raw PSID is masked or not primary when display name exists.
- No raw captions, full raw conversation history, tokens, secrets, or wholesale partner names.

### 7. Runbook Quality

Verify `docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md` is practical and includes:

- Purpose.
- Environment variable names only.
- Smoke test commands.
- Admin-only enable steps.
- Admin PSID test flow.
- First 30-minute watch checklist.
- Pause criteria.
- Rollback/disable steps.
- Clear statement of what is not live yet.

### 8. Tests

Verify required targeted tests and broad non-live V2 suite pass, or any skip is justified.

## Verdict Options

Use one:

- `GO`
- `GO_WITH_NOTES`
- `NO_GO`

## Required QA Report Sections

Write `docs/tasks/QA_REPORT_CURRENT.md` with:

1. Verdict
2. Scope reviewed
3. Checks matrix
4. Findings by severity
5. Tests verified
6. Residual risks / notes
7. Recommendation / next action

Then update `docs/tasks/AGENT_STATUS.json` with:

- `status`: `QA_GO`, `QA_GO_WITH_NOTES`, or `QA_NO_GO`
- `current_qa_task`: `QA-2026-05-19-010`
- `next_action`: `WAITING_FOR_CODEX`

Then stop.

## Hard Rules

- Do not modify runtime code.
- Do not modify migrations.
- Do not deploy.
- Do not touch V1 / Make.com / production webhook settings / secrets.
- Do not call live providers.
- If source files are unavailable or task files conflict, stop and report `BLOCKED`.
