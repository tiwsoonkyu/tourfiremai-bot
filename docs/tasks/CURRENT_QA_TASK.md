# CURRENT QA TASK

## Task ID
QA-2026-05-19-008

## Title
QA Review — Sprint 5 Package B: Source Attribution + LINE Admin Safety + Dashboard Read API v0

## Status
PENDING

## Assigned Role
Claude QA

## Controller
Codex

## Dev Task Under Review
DEV-2026-05-19-008

## Required Reading

Read:

1. `docs/AI_COMMAND_CENTER.md`
2. `docs/tasks/CURRENT_QA_TASK.md`
3. `docs/tasks/TASK_LOG.md`
4. `docs/tasks/DEV_REPORT_CURRENT.md`
5. `docs/tasks/AGENT_STATUS.json`
6. All files changed by DEV-2026-05-19-008

## QA Goal
Review the whole package as one integration unit. Do not split into micro reviews unless a P0 risk appears.

## Required Checks

1. Scope discipline:
   - V2 only.
   - No V1.
   - No Make.com.
   - No production webhook/deploy/secrets/live paid providers.

2. Source attribution:
   - Parser/adapter is conservative.
   - Unknown/absent source preserves old behaviour.
   - User message text cannot spoof a post/ad id.
   - `source_post_id`, `source_type`, and `source_platform` reach `Orchestrator.handle_turn(...)`.
   - Page-post sold-out signal can block the response through this path.

3. Prompt/data safety:
   - No raw full page captions injected into LLM payloads.
   - No wholesale partner names leak.
   - Planning notes remain compact.

4. LINE admin safety:
   - Allow-list is enforced before command parsing/execution.
   - Unauthorized sender has no side effects.
   - Authorized sender can pause/resume and mark/clear full where implemented.
   - No sensitive data is returned to unauthorized users.

5. Human handoff / pause:
   - Pause state prevents AI from continuing to answer where the code path covers it.
   - Resume returns control clearly.

6. Dashboard-safe read surface:
   - Requires explicit admin/auth context or equivalent guard.
   - Returns compact summaries.
   - Avoids full raw conversation history, raw captions, secrets, tokens, and raw PSID as primary display when a display name exists.

7. Tests:
   - Targeted tests cover the required behaviours.
   - Broad non-live suite passes or any skip is justified.

8. Migration:
   - No new migration unless clearly justified.
   - Migration 020 is assumed applied; do not apply migrations from QA.

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

- Read-only review unless Codex explicitly assigns a QA fix task.
- Do not modify runtime code.
- Do not deploy.
- Do not touch V1 / Make.com / production webhook / secrets.
- Do not call live paid providers.
