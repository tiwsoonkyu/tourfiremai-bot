# DEV REPORT — DEV-2026-05-19-008

## Status
READY_FOR_QA

## Task
Sprint 5 Package B — Source Attribution + LINE Admin Safety + Dashboard Read API v0.

## Summary

Implemented the three pieces of Sprint 5 Package B as one integration package:

1. **Source Attribution Adapter** (`v2/lib/source_attribution.py`) — a
   deterministic, no-Graph-API layer that inspects a Messenger / IG / LINE
   webhook event and decides whether the conversation came from
   `page_post`, `ad`, `organic`, or `unknown`. Validation of `post_id`
   happens DB-side via `page_post_context._page_post_row`, so an attacker-
   typed string cannot become a trusted page-post reference.
   `SourceAttribution.to_orchestrator_kwargs()` returns the exact kwargs
   `Orchestrator.handle_turn(..., source_post_id=..., source_type=...,
   source_platform=...)` already accepts (added in DEV-007).

2. **LINE Admin Allow-List Adapter** (`v2/lib/line_admin_adapter.py`) — a
   sender-id allow-list gate placed BEFORE `admin_command_handler`. Empty
   / missing / non-allowlisted senders never reach the parser, so denied
   commands have zero side effects. Supports
   `V2_STAGING_LINE_ADMIN_ALLOW_LIST` (comma / space / semicolon
   separated) with `V2_STAGING_LINE_ADMIN_USER_OR_GROUP_ID` as a single-
   admin fallback. Allow-list is injectable for tests
   (`AdminAllowList.from_iterable([...])`).

3. **Dashboard-Safe Read API v0** (`v2/lib/admin_dashboard_api.py`) — a
   service layer (not a Flask app) that exposes `list_cases`,
   `get_case`, `list_recent_posts`, `list_open_handoffs`. Every call
   requires an `AdminContext(allowed=True)` or is denied. All payloads
   are re-scrubbed for wholesale brand names, masked PSIDs, and capped
   titles (no raw captions, no raw conversation history, no secrets).

## Files Changed

```
v2/lib/source_attribution.py          (new)  367 lines
v2/lib/line_admin_adapter.py          (new)  276 lines
v2/lib/admin_dashboard_api.py         (new)  319 lines
v2/tests/test_source_attribution.py             (new)  213 lines
v2/tests/test_source_attribution_integration.py (new)  167 lines
v2/tests/test_line_admin_adapter.py             (new)  225 lines
v2/tests/test_admin_dashboard_api.py            (new)  239 lines
```

No existing files modified.
No migrations added.
No V1 files touched.
No `Make.com` blueprints touched.
No production webhook code modified.

## Implementation Details

### Source attribution adapter

Resolution order inside `extract_source(event, supabase)`:

1. Explicit caller `source_type` wins if it is one of
   `{page_post, ad, organic, unknown}`. If caller said `page_post` but the
   post id cannot be validated against `page_posts`, downgrade to
   `unknown` so we never claim provenance we did not earn.
2. If a candidate `post_id` is extracted from
   `message.reply_to.story.id` / `.story_id`,
   `postback.payload` starting `POST:`,
   `referral.ref` starting `POST:`,
   `entry.changes.value.post_id`, or top-level `source_post_id`, look it
   up in `page_posts`. A match → `page_post`. A miss → fall through.
3. Ad signals (`referral.source IN {ADS, CTM_ADS, IG_CTM_ADS,
   FACEBOOK_ADS}`, `referral.ad_id`, `postback.payload` starting `AD:`)
   → `ad`.
4. Any other normal messaging shape (sender / message / postback /
   referral present) → `organic`.
5. Otherwise → `unknown`.

`SourceAttribution.to_orchestrator_kwargs()` drops the `source_post_id`
at the boundary unless `page_post_validated=True`, which is the
invariant the orchestrator's planner relies on (the
`page_post_context.is_candidate_blocked` post-scope check trusts the
`source_post_id` it is given).

Platform inference: `facebook` (default) / `instagram` / `line` from
event `platform` / `object` / `source` fields. Caption text is never
read by this adapter — only ids and source tokens.

Hard caps: `MAX_REF_ID_LEN=200`; whitespace/control-chars rejected;
never raises (returns `unknown` on any internal error).

### LINE admin allow-list adapter

`AdminAllowList` is a frozen dataclass wrapping a `frozenset` of sender
ids. Construction normalises and rejects empty / whitespace-bearing /
oversize ids. `from_env` reads `V2_STAGING_LINE_ADMIN_ALLOW_LIST` first
(comma / space / semicolon separated), then falls back to
`V2_STAGING_LINE_ADMIN_USER_OR_GROUP_ID`. `to_dict()` only returns the
allowed count — never the raw ids.

`LineAdminAdapter.handle(sender_id, text, memory=None)`:

- Missing / malformed sender → `AdminCommandResult(ok=False, action='denied',
  error='missing_sender', mutated=False)`.
- Empty allow-list → `error='empty_allow_list'`.
- Sender not in allow-list → `error='not_allowed'`.
- Authorised → forwards to `admin_command_handler.handle_admin_command`
  with `admin_user_id = normalised_sender_id` so audit logs in
  `admin_ops.pause_bot_for_customer` record the actual caller.

Denials do NOT echo the original command text (no reflection of attacker
content) and do NOT leak allow-list membership.

### Dashboard-safe read API v0

`AdminContext(admin_user_id, allowed, source)` is the auth carrier.
`to_dict()` never includes the raw `admin_user_id` — only its
truthiness.

`AdminDashboardAPI`:

- `_gate(context)` denies missing or `allowed=False` contexts before any
  work is done.
- `list_cases`: caps `limit` at `_HARD_LIST_LIMIT=100`; defaults 20;
  delegates to `admin_ops.list_admin_cases` and re-projects each
  `AdminCaseSummary` via `_serialise_case` which re-scrubs wholesale
  brand names defensively.
- `get_case`: psid OR conversation_id; returns case_not_found cleanly.
- `list_recent_posts`: never returns `caption_text`; titles capped at
  `page_post_context.CONTEXT_TITLE_MAX_CHARS`.
- `list_open_handoffs`: masked PSIDs only; trigger detail summarised.

## Tests Run

- Targeted: `pytest v2/tests/test_source_attribution.py
  v2/tests/test_line_admin_adapter.py
  v2/tests/test_admin_dashboard_api.py
  v2/tests/test_source_attribution_integration.py` —
  **46 passed / 0 failed**.

- Broad non-live V2 suite:
  `pytest v2/tests/ --ignore=v2/tests/test_integration_staging.py
  --ignore=v2/tests/test_live_openai_health.py
  --ignore=v2/tests/test_phase2_live_followup.py` —
  **638 passed / 0 failed** (was 608 pre-change; +30 unique test files
  collected, 46 net new test cases including subtests).

- Live tests intentionally NOT exercised (staging Supabase /
  OpenAI / Phase 2 live followup) per task hard rules and per current
  shell lacking staging credentials.

## Scope/Safety Verification

- No V1 files modified (`grep -L` over `app.py`,
  `app_patched_*`, V1 paths). No Make.com blueprint changes.
- No migrations added under `v2/supabase/migrations/`.
- No production webhook code modified.
- No live Meta / FB / Instagram / LINE / OpenAI / OCR / paid-provider
  calls anywhere in new code or tests — verified by inspection (no
  `requests.post`, no `openai.*`, no `line_bot_sdk` usage).
- No secret patterns in new files (grep for
  `sk-|ghp_|EAAB|ya29|AKIA|password|secret_key`).
- No raw PSID strings, captions, tokens, or wholesale partner names in
  any returned payload from `AdminDashboardAPI` or
  `LineAdminAdapter` — covered by tests
  `test_returns_compact_summary_with_masked_psid`,
  `test_no_raw_caption_only_title`,
  `test_non_allowlisted_is_denied_with_no_side_effects`.
- `SourceAttribution.to_orchestrator_kwargs()` drops `source_post_id`
  unless validated — covered by
  `test_unverified_post_id_downgraded_to_unknown` and
  `test_orchestrator_kwargs_drops_unverified`.

## Risks / Notes

- The webhook (`v2/webhook/app.py`) does NOT yet call
  `extract_source` or invoke the orchestrator — Sprint 2 webhook
  remains the silent-ingest shape. Wiring the adapter into the live
  webhook path is the natural next Codex/Tiw decision (see Next Step).
  The orchestrator already accepts the kwargs, so the wiring is one
  line at the right place.
- `AdminAllowList.from_env` reads `V2_STAGING_*` env vars; it is safe
  to call but must NOT be called inside test bodies (tests use
  `AdminAllowList.from_iterable([...])` to avoid leaking real env).
- The new `LineAdminAdapter` does NOT itself send a LINE reply. It
  returns an `AdminCommandResult` whose `admin_text` is a Thai
  admin-facing string. A future LINE-OA webhook will be responsible
  for calling the LINE Messaging API to deliver `admin_text`.
- The dashboard API is a service layer only — no HTTP shim is included.
  A later sprint can add a Flask blueprint or another framework on top
  without changing the safety contract here.

## QA Checklist

QA reviewer should confirm:

1. `extract_source(event, supabase)` returns deterministic
   `SourceAttribution` for the four supported types and never returns a
   page-post type for an unvalidated post id.
2. `SourceAttribution.to_orchestrator_kwargs()` matches the kwarg
   signature of `Orchestrator.handle_turn`.
3. `LineAdminAdapter.handle(...)` rejects non-allowlisted senders with
   no DB row inserted (check `bot_pauses`, `handoffs`,
   `tour_availability_overrides`).
4. `AdminDashboardAPI` methods all gate on `AdminContext.allowed`.
5. Returned payloads never contain raw PSID, raw caption, wholesale
   partner names, or secret patterns.
6. Broad non-live V2 suite still 638 passed.
7. No existing tests broken (run targeted set:
   `test_orchestrator_planning.py`, `test_admin_command_handler.py`,
   `test_page_post_wiring.py`).

## Next Step

Codex should:

1. Review this Dev report + Diff for safety.
2. Decide whether to:
   - Open DEV-2026-05-19-009 to wire `extract_source` into the
     production webhook (`v2/webhook/app.py`) and call
     `Orchestrator.handle_turn(**attr.to_orchestrator_kwargs(), ...)`
     from the inbound event path, OR
   - Schedule the LINE-OA webhook adapter task that consumes
     `LineAdminAdapter.handle(...)` and sends `admin_text` back to the
     LINE Messaging API, OR
   - Schedule the HTTP shim (Flask or similar) that exposes
     `AdminDashboardAPI` as a guarded endpoint to the future admin
     dashboard.
3. Author QA-2026-05-19-008 task content to direct the QA pass.

