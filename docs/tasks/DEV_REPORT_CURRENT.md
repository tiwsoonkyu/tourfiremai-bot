# DEV REPORT — DEV-2026-05-19-009

## Status
READY_FOR_QA

## Task
Sprint 5 Package C — Runtime wiring for source attribution, LINE admin, and dashboard read API.

## Scope Implemented

1. **Meta webhook source-attribution seam** — `v2/webhook/app.py` now
   calls `extract_source(event, supabase)` for every accepted messaging
   event and persists the result as a `conversation_events` row with
   `event_type='source_attribution'`. Webhook stays silent-ingest (no
   outbound reply). Unverified attacker post ids are dropped at the
   boundary (validated DB-side against `page_posts`). The recorded
   attribution can be replayed into the orchestrator by a future caller —
   a test in `test_webhook_source_attribution.py` proves this end-to-end
   path blocks a sold-out post.

2. **LINE admin runtime entrypoint** — new `v2/webhook/admin_routes.py`
   adds `POST /admin/line`. Accepts JSON `{sender_id, text}`, dispatches
   through `LineAdminAdapter` (allow-list gated), returns the
   `AdminCommandResult` as JSON. No live LINE Messaging API call. Raw
   PSIDs scrubbed from every response via `_strip_raw_psid`. Oversize
   body rejected with 413.

3. **Dashboard read HTTP shim v0** — same module adds:
   - `GET /admin/dashboard/cases` (with `?only_paused=1`, `?limit=N`)
   - `GET /admin/dashboard/cases/<id>` (with `?by=psid|conversation_id`)
   - `GET /admin/dashboard/posts`
   - `GET /admin/dashboard/handoffs`
   - `GET /admin/healthz`

   Gated by `X-Admin-Token` header compared in constant time
   (`hmac.compare_digest`) against `app.config["V2_ADMIN_TOKEN"]`. Token
   is test-injected; in production it reads
   `V2_STAGING_DASHBOARD_TOKEN` via `_admin_token_from_config`. All
   payloads pass through `_strip_raw_psid` as a defensive top-level
   safety net.

## Files Changed

```
v2/webhook/app.py                              (modified, +82 lines)
v2/webhook/admin_routes.py                     (new, ~260 lines)
v2/tests/test_webhook_source_attribution.py    (new, ~270 lines)
v2/tests/test_line_admin_runtime.py            (new, ~220 lines)
v2/tests/test_admin_dashboard_runtime.py       (new, ~250 lines)
docs/tasks/DEV_REPORT_CURRENT.md               (this report)
docs/tasks/AGENT_STATUS.json                   (status flip to READY_FOR_QA)
```

No V1 files touched. No migrations added. No production webhook
settings changed (the new `/admin/*` routes are additive and read-only
modulo `LineAdminAdapter`'s own admin-gated mutations, which already
existed in DEV-008 and run against the same Supabase as before).

## Runtime Wiring Design

### Source attribution seam (silent-ingest)

```
Meta POST /webhook
    │
    ├── signature verify
    ├── per-event idempotency
    └── background thread → _process_event(event, full_message_id, trace_id)
            │
            ├── (existing) per-PSID lock + customer + conversation rows
            ├── (existing) intent classify + state-machine transition
            ├── (existing) inbound conversation_turns insert
            ├── (existing) state_change conversation_events row
            └── (new)   _record_source_attribution(event, ...)
                        │
                        ├── extract_source(event, supabase)
                        │   → validates `post_id` against page_posts
                        └── conversation_events.insert({
                              event_type='source_attribution',
                              event_data={source_type, source_post_id,
                                         source_platform, page_post_id,
                                         page_post_validated, raw_ref},
                              triggered_by='system',
                              meta_message_id=None  # avoid unique index
                                                   # collision with the
                                                   # state_change row
                            })
```

The orchestrator is **not** invoked from the webhook in this sprint.
The seam exists so that a future task can flip on orchestrator
invocation by reading the recorded source attribution and passing
`kwargs` through to `Orchestrator.handle_turn(..., source_post_id=...,
source_type=..., source_platform=...)`. The
`test_recorded_attribution_blocks_via_orchestrator` test proves this
replay works.

### LINE admin route

```
POST /admin/line
  body: {"sender_id": "<line_uid>", "text": "<command>"}
    │
    ├── size cap 4 KB → 413 body_too_large
    ├── JSON parse  → 400 invalid_json on failure
    ├── LineAdminAdapter.handle(sender_id, text)
    │     ├── normalise sender, reject whitespace / oversize
    │     ├── allow-list membership check (constant-time-ish via
    │     │   frozenset O(1))
    │     ├── on denial: return AdminCommandResult(ok=False,
    │     │   action='denied', error='missing_sender'|'empty_allow_list'
    │     │   |'not_allowed')  — NO side effects, NO command parse
    │     └── on allow: dispatch via existing admin_command_handler
    └── _strip_raw_psid(result.to_dict()) → JSON 200
```

### Dashboard shim

```
GET /admin/dashboard/*
  header X-Admin-Token: <token>
    │
    ├── _admin_token_from_config(app)  → 500 admin_token_not_configured
    │                                     when token absent
    ├── hmac.compare_digest(token, expected)
    │   → 401 missing_admin_token | invalid_admin_token
    ├── AdminContext(allowed=True, source='web')
    ├── AdminDashboardAPI.<list_cases|get_case|list_recent_posts|
    │                     list_open_handoffs>(...)
    │   (re-scrubs wholesale tokens, caps title, masks PSID)
    └── _strip_raw_psid(payload) → JSON 200
```

## Tests Run

- Targeted suite (the 8 files most likely to touch new code):

  ```
  pytest v2/tests/test_webhook.py
         v2/tests/test_source_attribution.py
         v2/tests/test_source_attribution_integration.py
         v2/tests/test_line_admin_adapter.py
         v2/tests/test_admin_dashboard_api.py
         v2/tests/test_webhook_source_attribution.py
         v2/tests/test_line_admin_runtime.py
         v2/tests/test_admin_dashboard_runtime.py
  ```
  → **77 passed / 0 failed**.

- Broad non-live V2 suite:

  ```
  pytest v2/tests/ \
    --ignore=v2/tests/test_integration_staging.py \
    --ignore=v2/tests/test_live_openai_health.py \
    --ignore=v2/tests/test_phase2_live_followup.py \
    -p no:cacheprovider -q
  ```
  → **662 passed / 0 failed** (was 638 pre-change; +24 new test
  cases).

- Live staging Supabase / OpenAI / Phase 2 live followup intentionally
  NOT exercised per task hard rules.

## Safety Checks

- **V1**: untouched (`grep -L` over `app.py`, `app_patched_*`,
  `webhook_proxy.py`, V1 paths).
- **Make.com**: no blueprint changes.
- **Production webhook settings**: no change. Meta verification handshake
  + signature behaviour identical.
- **Migrations**: none added.
- **Secrets**: no `sk-`, `ghp_`, `EAAB`, `ya29`, `AKIA` patterns in any
  new file. Admin token is injected at test time and read at runtime
  from `V2_STAGING_DASHBOARD_TOKEN` via `os.environ.get` — never logged
  and never returned in any response body.
- **Live API calls**: no `requests.post`, `openai.*`, `line_bot_api`,
  or any other paid-provider import in new code or tests.
- **Raw PSID leakage**: every JSON response from `/admin/*` is walked
  by `_strip_raw_psid` to drop any top-level `psid` key. The masked
  variant `psid_masked` is preserved. Verified by
  `test_authorized_cases_command`, `test_get_case_by_psid`,
  `test_list_open_handoffs_masks_psid`.
- **Wholesale partner names**: still scrubbed by
  `_WHOLESALE_BLACKLIST` via `admin_ops` / `page_post_context` upstream;
  the shim does not introduce a new free-text concatenation path.
- **Caption text**: never returned by `/admin/dashboard/posts` (verified
  by `test_list_recent_posts_caps_title_and_drops_caption`).
- **Body size**: `POST /admin/line` rejects >4 KB at the boundary.
- **Constant-time auth**: dashboard token compared via
  `hmac.compare_digest`.
- **Idempotency**: source-attribution conversation_events row inserted
  with `meta_message_id=None` so the unique partial index
  `(platform, meta_message_id) WHERE meta_message_id IS NOT NULL` does
  NOT collide with the existing state_change row that owns
  the inbound message id.

## Known Gaps / Next Recommended Action

1. **Orchestrator-from-webhook invocation is NOT yet enabled.** The
   recorded source attribution is the seam; a future task can flip a
   feature flag (or replace `_process_event`'s body) to call
   `Orchestrator.handle_turn(**kwargs)` from the inbound path. Test
   `test_recorded_attribution_blocks_via_orchestrator` proves the
   replay works end-to-end.
2. **LINE webhook signature verification not wired here.** The
   `/admin/line` route accepts a plain JSON body for now. A future task
   should wrap it with LINE's `X-Line-Signature` HMAC check before
   exposing the route to the public LINE webhook.
3. **Dashboard auth is a static token.** Acceptable for staging. A
   future task can swap to OAuth / Supabase RLS without changing the
   service contract `AdminDashboardAPI` exposes.
4. **Per-route logging** redacts sender ids via `_mask_sender`; admin
   token never appears in logs. Worth a separate observability sweep
   if needed.
5. **DLQ behaviour** is unchanged. The new source-attribution row is
   inside the same `try` block as the rest of `_process_event`; if its
   insert fails it logs and continues without re-raising, so it cannot
   itself escalate to DLQ.

## Stop Condition

Dev work complete. Awaiting QA / Codex review.

