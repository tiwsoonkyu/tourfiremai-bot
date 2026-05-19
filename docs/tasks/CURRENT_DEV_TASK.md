# CURRENT DEV TASK

## Task ID
DEV-2026-05-19-009

## Title
Sprint 5 Package C — Runtime Wiring for Source Attribution, LINE Admin, and Dashboard Read API

## Status
PENDING

## Assigned Role
Claude Dev

## Controller
Codex

## Context
Sprint 5 Package B is QA-cleared by owner-reported QA verdict `GO`.

Repository source of truth:

- GitHub repo: `github.com/tiwsoonkyu/tourfiremai-bot`
- Branch: `v2/s4-followup-vision-ondemand`
- This task must be executed against the GitHub branch files, not a stale Cowork-only workspace snapshot.
- If the task files or V2 source files are unavailable in the current workspace, stop and report `BLOCKED: source-of-truth repo unavailable`.

Already available:

- `v2/lib/source_attribution.py`
- `v2/lib/line_admin_adapter.py`
- `v2/lib/admin_dashboard_api.py`
- `Orchestrator.handle_turn(...)` already accepts `source_post_id`, `source_type`, and `source_platform`.
- Migration `20260519_020_page_post_intelligence.sql` has already been applied and verified on V2 staging by Codex.

Remaining practical gap:

- The V2 webhook/runtime does not yet call `extract_source(...)`.
- LINE admin messages do not yet have a safe webhook/service entrypoint.
- Dashboard read service does not yet have a minimal guarded HTTP/API shim.

## Goal
Wire the existing Sprint 5 adapters into V2 runtime surfaces in a staging-safe, test-first way so the system can be exercised end-to-end without touching V1 production, Make.com, production webhook settings, or live paid providers.

## Scope
Implement all subtasks in this package as one integrated Dev task. Do not stop after each small subtask unless you find a P0 risk.

### 1. Meta Webhook Source Attribution Wiring

Wire `v2.lib.source_attribution.extract_source(event, supabase)` into the V2 webhook processing path.

Requirements:

- In `v2/webhook/app.py`, call `extract_source(...)` for each accepted messaging event.
- Pass `attr.to_orchestrator_kwargs()` into `Orchestrator.handle_turn(...)` only if the current webhook path already calls or can safely instantiate the orchestrator.
- If the current V2 webhook is still silent-ingest only, add a narrow integration seam that records source attribution without enabling outbound customer replies.
- Unknown or absent source must preserve current behavior.
- User message text must not spoof post/ad IDs.
- Page-post sold-out/full signal must be able to reach planning logic through this path in tests.
- Keep Meta POST ack fast; do not add live Graph API calls.

### 2. LINE Admin Runtime Entry Point

Add a safe V2 admin entrypoint around `LineAdminAdapter`.

Requirements:

- No live LINE API calls in this task.
- Add a minimal route/service boundary suitable for a future LINE webhook.
- Enforce allow-list before command parsing/execution.
- Unauthorized sender must have no side effects and must not receive sensitive data.
- Authorized sender can execute supported commands via existing admin command core:
  - list/cases if supported
  - pause/resume customer if supported
  - mark_full / clear_full or equivalent sold-out commands if supported
- Return an `admin_text` payload from the route/service. Do not send to LINE yet.

### 3. Dashboard Read HTTP/API Shim v0

Expose `AdminDashboardAPI` through a minimal guarded runtime surface.

Requirements:

- If Flask is already used in `v2/webhook/app.py`, add guarded endpoints there or in a small blueprint/module imported by the app.
- Require explicit admin/auth guard. For now, a staging-only header/token check is acceptable if tests inject it safely.
- Endpoints should be read-only and compact:
  - list current cases
  - get one case
  - list recent page posts/status
  - list open handoffs
- Do not return full raw conversation history, raw captions, secrets, tokens, wholesale partner names, or raw PSID as the primary display when display name exists.
- No dashboard frontend in this task.

### 4. Tests

Add focused tests for:

- Webhook event with page-post source reaches orchestrator/planning kwargs or source-record seam.
- Unknown/organic event preserves old behavior.
- Spoofed post/ad id in user text is ignored.
- Sold-out post/tour can block response through runtime path where applicable.
- Unauthorized LINE admin sender cannot execute commands and causes no side effects.
- Authorized LINE admin sender can execute supported commands.
- Dashboard API guard denies unauthenticated reads.
- Dashboard API returns masked/scrubbed/capped payloads.
- Broad non-live V2 suite passes.

## Allowed Files
Prefer touching only:

- `v2/webhook/app.py`
- `v2/webhook/*.py` new small helper modules if needed
- `v2/lib/source_attribution.py` only if a runtime bug is found
- `v2/lib/line_admin_adapter.py` only if a runtime bug is found
- `v2/lib/admin_dashboard_api.py` only if a runtime bug is found
- `v2/tests/test_webhook*.py`
- `v2/tests/test_line_admin_runtime*.py`
- `v2/tests/test_admin_dashboard_runtime*.py`
- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

## Out of Scope

- Do not touch V1 production files.
- Do not touch Make.com.
- Do not change production webhook settings.
- Do not deploy.
- Do not call Meta Graph API.
- Do not call live LINE API.
- Do not call OpenAI / OCR / paid providers.
- Do not apply Supabase migrations.
- Do not build dashboard frontend UI.
- Do not enable live customer replies if the current V2 webhook is still silent-ingest only unless the existing V2 architecture explicitly supports it in tests.

## Required Tests

Run at minimum:

```bash
pytest v2/tests/test_webhook.py v2/tests/test_source_attribution*.py v2/tests/test_line_admin_adapter.py v2/tests/test_admin_dashboard_api.py -q
pytest v2/tests --ignore=v2/tests/test_integration_staging.py --ignore=v2/tests/test_live_openai_health.py --ignore=v2/tests/test_phase2_live_followup.py -p no:cacheprovider -q
```

If Windows temp permission blocks pytest, rerun with a repo-local `--basetemp`.

## Required Dev Report

Write `docs/tasks/DEV_REPORT_CURRENT.md` with:

1. Status
2. Scope implemented
3. Files changed
4. Runtime wiring design
5. Tests run and results
6. Safety checks
7. Known gaps / next recommended action

Then update `docs/tasks/AGENT_STATUS.json` and stop for QA.
