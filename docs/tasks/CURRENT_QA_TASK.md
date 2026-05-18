# Current QA Task

Task ID: `QA-2026-05-19-003`
Status: `PENDING`
Assigned role: Claude Cowork QA
Controller: Codex

## Task

Review Dev task `DEV-2026-05-19-003`.

This QA task is read-only. Do not patch code unless Codex explicitly asks for a QA patch.

## Context

Tiw approved adding a paid OCR / document parser helper, but only as an optional on-demand layer to improve PDF fee extraction accuracy.

The bot must remain safety-first:

- answer fee values only when reliable
- handoff when confidence is low
- never invent tip, deposit, single supplement, or visa values
- never lower thresholds just to make the bot answer more often

## Review Scope

Review:

1. `docs/AI_COMMAND_CENTER.md`
2. `docs/tasks/CURRENT_DEV_TASK.md`
3. `docs/tasks/CURRENT_QA_TASK.md`
4. `docs/tasks/TASK_LOG.md`
5. `docs/tasks/DEV_REPORT_CURRENT.md`
6. `docs/tasks/AGENT_STATUS.json`
7. All files changed by Dev

## QA Checks

Verify:

1. Dev stayed on V2 scope only.
2. V1 production code was not changed.
3. Make.com / Cloudflare / Meta production webhook behavior was not changed.
4. No secrets were written to files.
5. No live paid-provider calls are required by tests.
6. No live OpenAI calls are required by unit tests.
7. Document parser provider abstraction fails closed when credentials/provider are missing.
8. Benchmark path can run with a mock provider.
9. Fee thresholds were not weakened.
10. Fee answer policy still handoffs below threshold.
11. No wholesale partner names are introduced into prompts, logs, reports, cassettes, or customer-facing output.
12. Dev report clearly explains the accuracy/cost tradeoff and next step.

## Deliverable

Write:

`docs/tasks/QA_REPORT_CURRENT.md`

Update:

`docs/tasks/AGENT_STATUS.json`

Verdict must be one of:

- `GO`
- `GO WITH NOTES`
- `NO-GO`

## Stop Condition

After writing the QA report and AGENT_STATUS, stop and wait for Codex.
