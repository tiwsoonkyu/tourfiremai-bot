# CURRENT DEV TASK

## Task ID
DEV-2026-05-19-008

## Title
Sprint 5 Package B — Source Attribution + LINE Admin Safety + Dashboard Read API v0

## Status
PENDING

## Assigned Role
Claude Dev

## Controller
Codex

## Context
The previous package is QA-cleared:

- DEV/QA-2026-05-19-006: page post intelligence foundation.
- DEV/QA-2026-05-19-007: page post planning wired into response flow and admin command core.
- Migration 020 has been applied and verified on V2 staging Supabase by Codex.

Remaining Sprint 5 risks:

- Source attribution is not yet wired from webhook/adapter into `Orchestrator.handle_turn(...)`.
- LINE admin commands need an allow-list safety layer before they can be used by staff.
- Admin dashboard needs a safe read surface for current cases and post/tour status.

## Goal
Implement the next integration package so V2 can understand whether a conversation came from page post / ad / organic, let allowed staff pause/resume or mark tours full, and expose compact admin-safe case/status data for a future dashboard.

## Scope
You may implement all subtasks in this package as one integrated Dev task. Do not stop after each small subtask unless you find a P0 risk.

### 1. Source Attribution Adapter

Inspect existing V2 webhook/tests and add a deterministic source-attribution layer.

Requirements:

- Support source types: `page_post`, `ad`, `organic`, `unknown`.
- Extract only safe metadata from existing or test Meta-like payloads. Do not call Meta Graph API.
- Pass source data into:
  - `Orchestrator.handle_turn(..., source_post_id=..., source_type=..., source_platform=...)`
- Unknown or absent source must preserve current behaviour.
- Do not trust arbitrary user text as a post/ad id.
- Keep the planning context compact. Do not inject full captions into LLM prompts.

### 2. LINE Admin Allow-List Adapter Core

Add a deterministic adapter/service layer for admin commands.

Requirements:

- No live LINE API calls.
- Accept an admin sender id and text command.
- Reject non-allowlisted sender ids before parsing/executing admin commands.
- Authorized sender ids may execute existing admin command handler functions.
- Cover at least:
  - list/case read command if supported by existing handler.
  - pause/resume customer.
  - mark_full / clear_full or equivalent sold-out commands if supported.
- Unauthorized commands must have no side effects and must not leak sensitive data.
- Prefer injected allow-list config in tests. If environment variables are used, read only safe ids and never print secrets.

### 3. Dashboard-Safe Read API v0 / Service

Expose a minimal safe read surface for future admin dashboard.

Requirements:

- Use existing `admin_ops` functions where possible.
- Return compact current-case summaries and case detail suitable for dashboard.
- Must require an explicit admin/auth guard or injected admin context in tests.
- Avoid raw PSID as the primary display if customer display name exists.
- Do not return full raw conversation history, full raw captions, secrets, tokens, or wholesale partner names.
- If no web framework exists naturally in V2, implement a service/API boundary with tests instead of forcing a Flask app.

### 4. Tests

Add tests covering:

- Source attribution: page_post, ad, organic, unknown.
- Source post id reaches orchestrator planning.
- A post/tour marked full blocks recommendation through the adapter/orchestrator path.
- LINE non-allowlisted admin is denied with no side effects.
- LINE allowlisted admin can pause/resume and mark/clear full through safe handlers.
- Dashboard-safe read returns compact summaries and requires admin context.
- No raw caption, secret shape, wholesale partner name, or full PSID leaks from new public/admin surfaces.

Run targeted tests and broad non-live V2 suite.

## Hard Rules

- Do not modify V1.
- Do not touch Make.com.
- Do not deploy anything.
- Do not change production webhook settings.
- Do not call live Meta/FB/LINE/OpenAI/OCR/paid providers.
- Do not write secrets into files, logs, reports, or tests.
- Do not apply migrations to Supabase from Claude Dev.
- Avoid broad refactors. Keep changes V2-scoped.

## Expected Deliverables

1. Code and tests for the package above.
2. `docs/tasks/DEV_REPORT_CURRENT.md`
3. `docs/tasks/AGENT_STATUS.json`
4. If git is available, commit and push to `v2/s4-followup-vision-ondemand`. If not, state that clearly in the report and stop.

## Required Dev Report Sections

Write `docs/tasks/DEV_REPORT_CURRENT.md` with:

1. Summary
2. Files Changed
3. Implementation Details
4. Tests Run
5. Scope/Safety Verification
6. Risks / Notes
7. QA Checklist
8. Next Step

## Stop Condition

After writing the Dev report and status JSON, stop and wait for QA/Codex review.
