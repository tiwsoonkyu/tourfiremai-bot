# Current Dev Task

Task ID: `DEV-2026-05-19-006`
Status: `PENDING`
Assigned role: Claude Cowork Dev
Controller: Codex

## Task

Build the V2 Page Post Intelligence + Sold-Out Signal foundation.

Business need: most daily sales activity comes from Facebook page posts, ads, and organic chats. The AI must know what admins posted recently, remember at least the last 3 days of page posts, and respect admin sold-out/full signals before recommending a tour.

This task is foundation only. Do not call Meta Graph API, do not build the visual dashboard UI, and do not change production webhook behavior yet.

## Context

The current V2 priority is Sales Agent reliability before broader company automation.

Recent QA-cleared foundations:

- Admin handoff + pause/resume foundation
- Deterministic LINE admin command handler core
- PDF fee extraction safety foundations
- Tour canonical database and scraper foundation

New product invariant:

> If a customer comes from a recent page post/ad/organic source that points to a tour marked full/sold out by admin, the bot must not recommend that sold-out option. It should acknowledge the post context and offer the closest available alternatives.

## Scope

You may modify V2 code, migrations, tests, and docs only.

Required work:

1. Inspect existing V2 migrations, `v2/lib/admin_ops.py`, `v2/lib/admin_command_handler.py`, `v2/lib/orchestrator.py`, and current tests.
2. Add additive migration(s) after `20260518_019_*` for page-post memory and sold-out override storage.
3. Add a pure deterministic V2 service module for page post context and sold-out decisions.
4. Add unit tests with no live network calls and no real credentials.
5. Update docs with a concise plan/contract for future dashboard and Meta source attribution wiring.

Suggested implementation shape:

- Migration: `v2/supabase/migrations/20260519_020_page_post_intelligence.sql`
- Module: `v2/lib/page_post_context.py`
- Tests: `v2/tests/test_page_post_context.py`

Suggested tables or equivalent:

- `page_posts`
  - platform, page_id, post_id, permalink_url, posted_at, text_hash, caption_text, status, active_until
  - default relevance window: 3 days from `posted_at`
- `page_post_tour_links`
  - post_id reference, web_code, tour_code_real, tour_id/tour canonical reference if available, confidence, status
- `tour_availability_overrides`
  - web_code/tour_code_real/tour_id, status (`available`, `sold_out`, `full`, `unknown`), scope (`tour`, `departure`, `post`), reason, marked_by, marked_at, expires_at

Use the existing schema style if there is a better local pattern.

## Required Behaviors

1. Store/update recent page posts idempotently by `(platform, post_id)`.
2. Detect tour references from page post text:
   - tour URL such as `/tour/ap123456`
   - web code such as `ap123456`
   - real tour code if present
3. `list_recent_page_posts(days=3)` returns only active posts in the recent window by default.
4. Admin can mark a linked tour/post/departure as `sold_out` or `full` through a pure function.
5. Admin can clear a sold-out/full override through a pure function.
6. A response-planning helper can answer:
   - source type: `page_post`, `ad`, `organic`, or `unknown`
   - recent post context if available
   - whether a candidate tour must be blocked because it is sold out/full
   - safe Thai admin/bot-facing reason text
7. If a tour from a recent post is sold out/full, return a deterministic replacement-needed signal instead of silently offering it.
8. Do not expose wholesale partner names.
9. Do not put full post history into LLM context. Return a compact deterministic context summary only.
10. Do not make live Meta/Facebook, LINE, OpenAI, OCR, Supabase production, or paid-provider calls in tests.

## Future Work Out of Scope

Do not implement these in this task:

- Live Meta Graph API page-post ingestion
- Visual admin dashboard UI
- Production webhook source-attribution wiring
- Real LINE admin command adapter
- Production deploy
- V1 or Make.com changes

This task should prepare the data model and deterministic service layer so those follow-up tasks are small and safe.

## Required Tests

Add or update tests for:

1. Upsert page post idempotency.
2. 3-day recent-post filtering.
3. Extraction of web code from tour URLs and plain text.
4. Extraction of real tour code when present.
5. Linking a page post to one or more tours.
6. Marking a linked tour/post as `sold_out`.
7. Clearing a sold-out/full override.
8. Candidate tour blocking when sold-out/full override exists.
9. Candidate tour allowed when no override exists or override expired.
10. Source context: page post vs ad vs organic.
11. Compact context summary does not include excessive post text.
12. No secrets or wholesale partner names in generated admin/bot text.

Run targeted tests and the broad non-live V2 suite if feasible.

## Deliverable

Write:

`docs/tasks/DEV_REPORT_CURRENT.md`

Update:

`docs/tasks/AGENT_STATUS.json`

Commit and push your changes to:

`v2/s4-followup-vision-ondemand`

## Dev Report Format

Include:

1. Status
2. Files changed
3. Root cause / business need
4. Summary of changes
5. Tests run
6. Risks / assumptions
7. What QA should verify
8. Next recommended step

## Stop Condition

After writing the report/status and pushing, stop. Do not proceed to QA yourself.
