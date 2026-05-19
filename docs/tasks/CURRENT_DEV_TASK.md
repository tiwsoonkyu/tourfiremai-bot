# Current Dev Task

Task ID: `DEV-2026-05-19-007`
Status: `PENDING`
Assigned role: Claude Cowork Dev
Controller: Codex

## Task

Wire the QA-cleared Page Post Intelligence foundation into the V2 sales-agent planning layer and deterministic admin command core.

This task is the next safe step after `DEV-2026-05-19-006` / `QA-2026-05-19-006`.

Important operational note:

- Migration `20260519_020_page_post_intelligence.sql` has QA GO, but Codex has not applied it to Supabase staging yet because the Supabase connector requires re-authentication and local staging DB credentials are not available in this Codex shell.
- Dev must write code/tests so the work is locally testable with in-memory fakes and does not require live Supabase.
- Do not attempt to connect to Supabase from Claude Cowork.

## Business Goal

TourFireMai admins post tours on the Facebook page daily. Customer chats can come from:

- Recent page posts
- Ads
- Organic inbox messages

The AI must use source context before recommending tours. If a customer came from a post/ad tied to a tour that admin marked `full` or `sold_out`, the bot must not recommend that tour. It should explain safely and offer available alternatives.

Admin also needs deterministic commands before the dashboard UI exists:

- See recent page posts
- Mark a tour/post/departure as full
- Clear a full/sold-out override

## Scope

You may modify V2 code, tests, and docs only.

Allowed likely files:

- `v2/lib/orchestrator.py`
- `v2/lib/response_writer.py`
- `v2/lib/admin_command_handler.py`
- `v2/lib/page_post_context.py` only if a tiny helper is required
- `v2/tests/*`
- `docs/*`

Do not add a new migration in this task unless absolutely necessary. The storage layer was created in migration 020.

## Required Work

1. Inspect:
   - `v2/lib/page_post_context.py`
   - `v2/lib/admin_command_handler.py`
   - `v2/lib/orchestrator.py`
   - `v2/lib/response_writer.py`
   - related tests

2. Add deterministic admin command support for page-post/sold-out operations:
   - `posts`
   - `post <post_id>` or `post <short id>` if local patterns support it
   - `mark_full <web_code|tour_code|post_id> [reason]`
   - `mark_sold_out <web_code|tour_code|post_id> [reason]`
   - `clear_full <web_code|tour_code|post_id>`
   - `clear_sold_out <web_code|tour_code|post_id>`

   Keep command parsing conservative. If the target is ambiguous, return a safe message asking admin to specify `web_code`, real tour code, or post id.

3. Wire page-post planning context into the sales-agent response path:
   - Before writing a recommendation, collect compact source context via the page-post service where available.
   - If `replacement_needed` / blocked candidate is returned, do not recommend the blocked tour.
   - Add a deterministic planning note for the response writer: source type, recent post title/code, blocked reason, and replacement requirement.
   - Keep LLM context compact. Do not dump full post captions.

4. Preserve core invariants:
   - LLM must not decide sold-out/full semantics.
   - Sold-out/full decisions must come from deterministic code.
   - Do not mention wholesale partner names.
   - Do not confirm seats or final price.
   - Do not expose full post history to the LLM.

5. Add tests that prove the wiring works without live services.

## Required Tests

Add/update unit tests for:

1. Admin command `posts` returns recent-post summaries only, not full captions.
2. Admin command `mark_full <web_code>` calls the page-post/sold-out service and returns a safe Thai/English admin-facing confirmation.
3. Admin command `clear_full <web_code>` clears the override.
4. Ambiguous admin command target returns a safe clarification message.
5. Response planning blocks a candidate tour marked `full`.
6. Response planning blocks a candidate from a marked-full page post.
7. Response planning allows a candidate when no override exists.
8. Response planning emits compact context only.
9. The response writer does not recommend a blocked tour.
10. No wholesale partner names or secrets leak in new admin/bot text.

Run targeted tests and the broad non-live V2 suite if feasible.

## Out of Scope

Do not implement:

- Live Meta Graph API page-post ingestion
- Live Facebook/Meta source-attribution webhook parsing
- Visual dashboard UI
- Real LINE Messaging API send/receive adapter
- Production deploy
- Supabase production access
- V1 or Make.com changes
- Live paid OpenAI/OCR/provider calls

## Deliverable

Write:

`docs/tasks/DEV_REPORT_CURRENT.md`

Update:

`docs/tasks/AGENT_STATUS.json`

Commit and push changes to:

`v2/s4-followup-vision-ondemand`

If Claude Cowork has no `.git` checkout, write the files in the shared workspace and stop. Codex will copy/commit/push.

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

After writing the report/status and pushing if possible, stop. Do not proceed to QA yourself.
