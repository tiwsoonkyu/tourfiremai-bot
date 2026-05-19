# CURRENT QA TASK

## Task ID
QA-2026-05-19-009

## Title
QA Review — Sprint 5 Package C Runtime Wiring

## Status
PENDING

## Assigned Role
Claude QA

## Controller
Codex

## Dev Task Under Review
DEV-2026-05-19-009

## Required Reading

Use GitHub branch `v2/s4-followup-vision-ondemand` in `github.com/tiwsoonkyu/tourfiremai-bot` as source of truth. If local Cowork workspace files differ or required source files are missing, stop and report `BLOCKED: source-of-truth repo unavailable`.

Read:

1. `docs/AI_COMMAND_CENTER.md`
2. `docs/tasks/CURRENT_QA_TASK.md`
3. `docs/tasks/TASK_LOG.md`
4. `docs/tasks/DEV_REPORT_CURRENT.md`
5. `docs/tasks/AGENT_STATUS.json`
6. All files changed by DEV-2026-05-19-009

## QA Goal
Review the runtime wiring as one integration unit. Do not split into micro reviews unless a P0 risk appears.

## Required Checks

1. Scope discipline:
   - V2 only.
   - No V1.
   - No Make.com.
   - No production webhook settings/deploy/secrets/live paid providers.

2. Meta webhook source attribution:
   - `extract_source(...)` is called from the intended V2 runtime path or an explicitly safe source-record seam exists.
   - Unknown/absent source preserves old behavior.
   - User text cannot spoof source IDs.
   - Page-post source can reach planning/sold-out logic in tests where applicable.
   - Meta ack path remains fast and does not call Graph API.

3. LINE admin runtime:
   - Allow-list gate executes before command parsing/execution.
   - Unauthorized sender causes no side effects and no sensitive data leak.
   - Authorized sender can execute supported commands.
   - No live LINE send happens in tests or runtime code added in this task.

4. Dashboard read API:
   - Requires explicit admin/auth guard.
   - Returns compact payloads only.
   - Masks PSID where appropriate.
   - Does not return raw captions, full raw conversation history, secrets, tokens, or wholesale partner names.

5. Human handoff / pause safety:
   - Existing pause/handoff semantics are not weakened.
   - Admin commands do not accidentally unpause or mark paid.

6. Tests:
   - Required targeted tests cover runtime path, safety gates, and dashboard guard.
   - Broad non-live suite passes or any skip is justified.

7. Migration/deploy:
   - No migration applied by QA.
   - No deployment performed.

## Verdict Options

Use one:

- `GO`
- `GO_WITH_NOTES`
- `NO_GO`

## Required QA Report Sections

Write `docs/tasks/QA_REPORT_CURRENT.md` with:

1. Verdict
2. Scope Reviewed
3. Checks Matrix
4. Findings by Severity
5. Tests Verified
6. Residual Risks / Notes
7. Recommendation / Next Action

Then update `docs/tasks/AGENT_STATUS.json` and stop.

## Hard Rules

- Do not modify runtime code.
- Do not modify migrations.
- Do not deploy.
- Do not touch V1 / Make.com / production webhook settings / secrets.
- If source files are unavailable or task files conflict, stop and report `BLOCKED`.
