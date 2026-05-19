# Sprint 5 Admin-Only Real Chat Runbook

Status: draft-ready for QA review
Scope: V2 staging only. Do not connect the production Meta webhook yet.

## Goal

Let Tiw/admin test the real Messenger webhook path with only approved PSIDs while keeping all real customers filtered out. This proves:

- Meta signature verification works.
- Inbound message ingest works.
- Source attribution can record page-post / ad / organic context.
- Admin commands can pause/resume and mark tours full.
- Dashboard-safe read endpoints do not expose raw PSIDs or secrets.

## Required Environment Variables

Set values only in the staging service secret store. Do not commit values.

```bash
V2_ADMIN_ONLY_TEST_MODE=true
V2_STAGING_ADMIN_TEST_PSID_ALLOW_LIST=<comma-separated admin/test PSIDs>
V2_STAGING_DASHBOARD_TOKEN=<random admin dashboard token>
V2_STAGING_LINE_ADMIN_ALLOW_LIST=<comma-separated LINE admin user/group ids>
V2_STAGING_SUPABASE_URL=<staging Supabase URL>
V2_STAGING_SUPABASE_SERVICE_ROLE_KEY=<staging service role key>
V2_STAGING_REDIS_URL=<staging Redis URL>
V2_STAGING_FB_APP_SECRET=<Meta app secret for staging app>
V2_STAGING_FB_VERIFY_TOKEN=<staging verify token>
```

Optional aliases supported by the gate:

```bash
V2_STAGING_ADMIN_TEST_PSIDS=<comma-separated admin/test PSIDs>
V2_STAGING_LINE_ADMIN_USER_OR_GROUP_ID=<single LINE admin id>
```

## Local Smoke Commands

Run from the repo root.

```bash
.\.venv_codex\Scripts\python.exe -m pytest v2/tests/test_admin_only_runtime_smoke.py -q -p no:cacheprovider --basetemp=.pytest_tmp_smoke

.\.venv_codex\Scripts\python.exe -m pytest `
  v2/tests/test_webhook.py `
  v2/tests/test_webhook_source_attribution.py `
  v2/tests/test_line_admin_runtime.py `
  v2/tests/test_admin_dashboard_runtime.py `
  v2/tests/test_admin_only_runtime_smoke.py `
  -q -p no:cacheprovider --basetemp=.pytest_tmp_target
```

Broad non-live suite:

```bash
.\.venv_codex\Scripts\python.exe -m pytest v2/tests `
  --ignore=v2/tests/test_integration_staging.py `
  --ignore=v2/tests/test_live_openai_health.py `
  --ignore=v2/tests/test_phase2_live_followup.py `
  -q -p no:cacheprovider --basetemp=.pytest_tmp
```

## Readiness Check

Call the admin runtime config endpoint with the dashboard token:

```bash
curl -H "X-Admin-Token: $V2_STAGING_DASHBOARD_TOKEN" \
  https://<v2-staging-url>/admin/runtime-config
```

Expected:

- `admin_only_test_mode = enabled`
- `admin_test_psid_allow_list = configured`
- `admin_test_psid_allow_list_count >= 1`
- `dashboard_admin_token = configured`
- `line_admin_allow_list = configured`
- `supabase_staging_url = configured`
- `fb_app_secret = configured`
- `fb_verify_token = configured`

The response must never include token values, raw PSIDs, or secrets.

## Admin-Only Test Flow

1. Keep `V2_ADMIN_ONLY_TEST_MODE=true`.
2. Add only Tiw/admin PSIDs to `V2_STAGING_ADMIN_TEST_PSID_ALLOW_LIST`.
3. Point the staging Meta app webhook to the V2 staging URL.
4. Send a Messenger message from the allow-listed admin account.
5. Verify:
   - `/webhook` response has `scheduled: 1`, `filtered: 0`.
   - Customer/conversation/turn rows are created in staging.
   - No outbound customer message is sent unless an explicit later sprint enables response delivery.
6. Send a Messenger message from a non-allowlisted test account if available.
7. Verify:
   - `/webhook` response has `scheduled: 0`, `filtered: 1`.
   - No customer/conversation/turn rows are created for that PSID.

## Source Context Test

Seed a page post in staging, then send a test event carrying a Meta-owned post ref.

Expected:

- `conversation_events.event_type = source_attribution`
- `event_data.source_type = page_post`
- `event_data.page_post_validated = true`
- Raw customer text must never be trusted as a post id.

## Admin Commands

Authorized LINE admin commands:

```text
cases
pause <psid> <reason>
resume <psid> <reason>
mark_full <web_code>
clear_full <web_code>
```

Safety expectations:

- Non-allowlisted LINE sender is denied with no mutation.
- Authorized `pause` prevents AI takeover for that customer.
- Authorized `resume` clears the pause.
- Command responses must not expose raw PSIDs.

## First 30-Minute Watch

Check every 5 minutes:

- Any non-admin PSID processed? If yes: disable webhook immediately.
- Any outbound customer-facing reply sent from V2? If yes: disable webhook immediately.
- Any dashboard response shows raw PSID or secret? If yes: disable dashboard endpoint.
- Any admin command mutates from non-allowlisted sender? If yes: disable `/admin/line`.
- Any source attribution accepts user-typed post ids? If yes: disable source-context planning.

## Pause Criteria

Immediate pause:

- Non-allowlisted customer gets processed.
- V2 sends a customer reply before response delivery is approved.
- Admin token leaks.
- Raw PSID appears in dashboard payload.
- A non-admin LINE sender can pause/resume/mark full.

Watch but do not stop:

- Source type is `organic` when expected `page_post`.
- Runtime config says one optional item is missing, while admin-only filtering still works.

## Rollback / Disable

Fastest safe rollback:

1. Set `V2_ADMIN_ONLY_TEST_MODE=false` only if the webhook is not connected to public traffic.
2. To stop all V2 intake, remove the Meta webhook subscription from the staging app or point it back to a known-safe endpoint.
3. Rotate the dashboard token if it was shared outside the admin group.
4. Remove all IDs from `V2_STAGING_ADMIN_TEST_PSID_ALLOW_LIST`.

## Not Live Yet

This runbook does not approve:

- Connecting V2 to the production Meta page webhook.
- Sending customer-facing V2 replies.
- Live OpenAI/OCR/paid-provider calls.
- Reading production customer data.
- Re-enabling Make.com.
