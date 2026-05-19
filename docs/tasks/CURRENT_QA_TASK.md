# Current QA Task

Task ID: `QA-2026-05-19-006`
Status: `PENDING`
Assigned role: Claude Cowork QA
Controller: Codex

## Task

Review Dev task `DEV-2026-05-19-006`.

This QA task is read-only. Do not patch code unless Codex explicitly asks for a QA patch.

## Context

`DEV-2026-05-19-006` should add a V2-only Page Post Intelligence + Sold-Out Signal foundation.

The business problem:

- Admin posts tours on the Facebook page daily.
- Customers may chat from page posts, ads, or organic messages.
- The AI should remember recent page posts for at least 3 days.
- Admin needs a future dashboard control to mark a posted tour as full/sold out.
- The bot must not recommend a tour that admin marked as full/sold out.

This QA task verifies the foundation only. Live Meta ingestion, visual dashboard UI, production webhook source attribution, and deployment are out of scope.

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
6. Migrations are additive and do not reset/drop existing staging data.
7. Page posts can be upserted idempotently by platform/post id.
8. Recent-post filtering defaults to at least the last 3 days.
9. Tour references are extracted from post text:
   - `/tour/ap123456`
   - `ap123456`
   - real tour code when present
10. Admin sold-out/full override can be set and cleared.
11. Sold-out/full override blocks a candidate tour deterministically.
12. Expired or absent override allows a candidate tour.
13. Source context distinguishes page post, ad, organic, and unknown.
14. Compact context summary avoids dumping excessive post text into LLM context.
15. Generated admin/bot reason text contains no secrets and no wholesale partner names.
16. Tests cover all required behaviors.
17. Broad non-live V2 suite passes or any skips/failures are clearly justified.

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
