# TourFireMai Claude Skills

Use these short skills inside Claude Cowork Project sessions so Dev, QA, and Controller agents follow the same operating model without repeatedly pasting long instructions.

## Recommended Setup

Create three separate Claude Project skills or saved prompts:

1. `TourFireMai Dev` from `TOURFIREMAI_DEV_SKILL.md`
2. `TourFireMai QA` from `TOURFIREMAI_QA_SKILL.md`
3. `TourFireMai Controller` from `TOURFIREMAI_CONTROLLER_SKILL.md`

Keep the main Project Instructions short. Do not paste full business memory, secrets, webhook URLs, API keys, or old V1 Make.com details into the skill.

## Session Pattern

- Dev session: use when implementing the current `CURRENT_DEV_TASK.md`.
- QA session: use after Dev writes `DEV_REPORT_CURRENT.md`.
- Controller is normally Codex. Use the Claude Controller skill only if Codex explicitly asks Claude to update task files or summarize status.

## Source Of Truth

The repo task files always win over chat instructions:

- `docs/AI_COMMAND_CENTER.md`
- `docs/tasks/CURRENT_DEV_TASK.md`
- `docs/tasks/CURRENT_QA_TASK.md`
- `docs/tasks/TASK_LOG.md`
- `docs/tasks/AGENT_STATUS.json`

If task files are missing, stale, or conflict with chat, stop and report `BLOCKED`.
