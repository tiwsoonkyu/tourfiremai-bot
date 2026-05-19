# TourFireMai AI Command Center

Last updated: 2026-05-18
Owner: Tiw
Controller: Codex
Implementer: Claude Cowork Dev
Reviewer: Claude Cowork QA

## Purpose

This file is the single source of truth for coordinating work between Codex and Claude Cowork.

Codex updates the task files in this repository. Claude Cowork reads those files, performs only the assigned work, writes reports, and stops for review.

## Operating Model

```text
Tiw
  -> decides business direction and approves risk

Codex
  -> acts as PM / Architect / Controller
  -> updates task files
  -> reviews Claude reports and diffs
  -> decides GO / NO-GO recommendations

Claude Dev
  -> reads docs/tasks/CURRENT_DEV_TASK.md
  -> implements only the current approved task
  -> writes a completion report

Claude QA
  -> reads docs/tasks/CURRENT_QA_TASK.md
  -> reviews Dev output read-only unless told otherwise
  -> writes a QA verdict
```

## Active Priority

Current priority is Sales Agent reliability before broader company automation.

Priority order:

1. Customer memory continuity
2. Accurate tour code / tour data / PDF fee data
3. Page post / ads / organic source context, including recent page posts and sold-out signals
4. Human handoff without AI interruption
5. Admin dashboard v0 for control and visibility
6. Cost visibility and budget guardrails

Later modules:

- Ads agent
- Content agent
- Marketing agent
- Manager agent

## Current Execution Rule

Claude Cowork must always read these files before doing work:

1. `docs/AI_COMMAND_CENTER.md`
2. `docs/tasks/CURRENT_DEV_TASK.md`
3. `docs/tasks/CURRENT_QA_TASK.md`
4. `docs/tasks/TASK_LOG.md`
5. `docs/tasks/AGENT_STATUS.json`

If these files conflict with an older chat instruction, these files win.

## Work States

Use these task states in reports:

- `PENDING`
- `IN_PROGRESS`
- `BLOCKED`
- `READY_FOR_QA`
- `QA_IN_PROGRESS`
- `QA_GO`
- `QA_GO_WITH_NOTES`
- `QA_NO_GO`
- `DONE`

## Hard Safety Rules

Unless a task explicitly says otherwise:

- Do not modify V1 production behavior.
- Do not reactivate Make.com.
- Do not change production Messenger webhook.
- Do not deploy to production.
- Do not rotate or print secrets.
- Do not change sales logic without a task.
- Do not change customer memory behavior without a task.
- Do not change PDF extraction behavior without a task.
- Do not change handoff behavior without a task.
- Do not make live paid-provider calls unless a task explicitly approves a live run and defines a budget cap.

## Required Dev Report Format

Claude Dev must write a report with:

1. Status
2. Files changed
3. Summary of changes
4. Tests run
5. Risks / assumptions
6. What QA should verify
7. Next recommended step

Required location:

`docs/tasks/DEV_REPORT_CURRENT.md`

## Required QA Report Format

Claude QA must write a report with:

1. Verdict: `GO`, `GO WITH NOTES`, or `NO-GO`
2. Scope reviewed
3. Evidence checked
4. Findings by severity
5. Tests verified
6. Remaining risks
7. Next recommended step

Required location:

`docs/tasks/QA_REPORT_CURRENT.md`

## Handoff Rule Between Agents

Dev must stop after writing its report.

QA must not silently continue implementation. If QA finds a problem, QA writes the smallest required fix recommendation and stops.

Codex decides whether to patch, ask Dev to patch, or ask Tiw for a decision.

## Overnight Work Rule

For work while Tiw is offline:

- Dev may continue only within the current task scope.
- QA may review only the completed Dev output.
- If a blocker requires a business decision, stop and mark `BLOCKED`.
- If a task touches production, payment, customer data, or public webhook behavior, stop and wait for Tiw/Codex approval.
