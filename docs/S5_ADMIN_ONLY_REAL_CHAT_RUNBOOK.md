# Sprint 5 Admin-Only Real Chat Runbook

Status: finalized for DEV-2026-05-20-016 (Sprint 5 Package J)
Scope: **V2 staging only.** Do **NOT** point the production Messenger webhook at the V2 staging URL while running this runbook.

> 1. Staging-only warning
>
> This runbook is **only** valid against V2 staging. V1 production must not be touched. Production Meta webhook settings must not be changed. Make.com must not be reactivated. No customer-facing V2 outbound replies are approved yet. If at any point V2 produces an outbound reply to a customer, treat it as a P0 incident and disable the staging webhook immediately.

## Goal

Let Tiw / admin test the real Messenger webhook path with **only allow-listed PSIDs** while every real customer is filtered out. This proves:

- Meta signature verification works against the V2 staging app.
- Inbound message ingest works (turn rows are persisted).
- Source attribution can record page-post / ad / organic context deterministically.
- Admin commands can pause/resume and mark tours full.
- Dashboard-safe read endpoints never expose raw PSIDs, raw tokens, or secrets.

It does **NOT** approve sending any customer-facing reply.

---

## 2. Required Environment Variables

Set values only in the staging service secret store. **Never commit values. Never paste values into screenshots or shared docs.** This runbook only ever references the variable NAMES.

| Variable | Purpose |
|----------|---------|
| `V2_ADMIN_ONLY_TEST_MODE` | Master switch. `true` filters every non-allow-listed PSID. |
| `V2_STAGING_ADMIN_TEST_PSID_ALLOW_LIST` | Comma-separated admin/test PSIDs that may be processed. |
| `V2_STAGING_ADMIN_TEST_PSIDS` | (Optional alias for the line above - same parsing.) |
| `V2_STAGING_DASHBOARD_TOKEN` | Bearer token for `X-Admin-Token` on `/admin/*` routes. |
| `V2_STAGING_LINE_ADMIN_ALLOW_LIST` | Comma-separated LINE admin user/group IDs that may issue admin commands. |
| `V2_STAGING_LINE_ADMIN_USER_OR_GROUP_ID` | (Optional alias for a single ID.) |
| `V2_STAGING_SUPABASE_URL` | Staging Supabase URL. |
| `V2_STAGING_SUPABASE_SERVICE_ROLE_KEY` | Staging service-role key. |
| `V2_STAGING_REDIS_URL` | Staging Redis URL. |
| `V2_STAGING_FB_APP_SECRET` | Meta app secret for the staging app. Used to verify X-Hub-Signature-256. |
| `V2_STAGING_FB_VERIFY_TOKEN` | Meta verify token for the GET handshake. |

> Live-provider keys are intentionally **not required** for admin-only preflight:
> `V2_STAGING_OPENAI_API_KEY`, `V2_STAGING_OPENAI_*_MODEL`, `V2_STAGING_OCR_PROVIDER_KEY`,
> `V2_STAGING_DOCUMENT_PARSER_KEY`. The preflight tool reports them as `not_required`.

---

## 3. Runtime-Config Check and Expected Safe Statuses

```bash
curl -H "X-Admin-Token: $V2_STAGING_DASHBOARD_TOKEN" \
  https://<v2-staging-url>/admin/runtime-config
```

Expected JSON (status strings only - **no values**):

```json
{
  "ok": true,
  "status": "ok",
  "checks": {
    "admin_only_test_mode": "enabled",
    "admin_test_psid_allow_list": "configured",
    "admin_test_psid_allow_list_count": 1,
    "dashboard_admin_token": "configured",
    "line_admin_allow_list": "configured",
    "supabase_staging_url": "configured",
    "fb_app_secret": "configured",
    "fb_verify_token": "configured"
  }
}
```

Safety rules for the response itself:

- The response must **never** include any token value, raw PSID, or secret.
- `admin_test_psid_allow_list_count` must be `>= 1`.
- If `admin_only_test_mode = enabled` and `admin_test_psid_allow_list = missing`, the webhook will fail closed and filter every inbound - restore the env var before re-testing.

You can run the same check fully offline (no HTTP) using the preflight CLI:

```bash
python -m v2.tools.admin_only_preflight              # text report
python -m v2.tools.admin_only_preflight --json       # machine-readable
```

Exit code `0` = ready; `1` = at least one required item missing or admin-only mode disabled.

---

## 4. Local Smoke Commands

Run from the repo root. These never call Meta, LINE, OpenAI, OCR providers, Supabase, or Redis - they exercise the V2 app with in-memory fakes.

Prerequisite for the webhook smoke tests: install the test dependencies, including Flask, before running this section.

### Targeted admin-only smoke

```bash
python -m pytest v2/tests/test_admin_only_runtime_smoke.py -q \
  -p no:cacheprovider --basetemp=.pytest_tmp_smoke
```

### Adjacency suite required by DEV-016

```bash
python -m pytest \
  v2/tests/test_webhook.py \
  v2/tests/test_webhook_source_attribution.py \
  v2/tests/test_line_admin_runtime.py \
  v2/tests/test_admin_dashboard_runtime.py \
  v2/tests/test_admin_only_runtime_smoke.py \
  v2/tests/test_signed_meta_webhook_smoke.py \
  -q -p no:cacheprovider --basetemp=.pytest_tmp_target
```

### Broad non-live V2 suite

```bash
python -m pytest v2/tests \
  --ignore=v2/tests/test_integration_staging.py \
  --ignore=v2/tests/test_live_openai_health.py \
  --ignore=v2/tests/test_phase2_live_followup.py \
  -q -p no:cacheprovider --basetemp=.pytest_tmp
```

Note: Windows users with PowerShell should swap `\` line-continuations for backticks.

---

## 5. Signed Meta Webhook Smoke Procedure

DEV-016 adds an offline-safe helper:

```text
v2/tools/signed_meta_webhook_smoke.py
```

Default behaviour: dry-run only. The helper computes the same `X-Hub-Signature-256` value the V2 webhook verifies (HMAC-SHA256 of the JSON body with `V2_STAGING_FB_APP_SECRET`) and prints a **redacted** curl preview. **No HTTP request is sent** unless the operator passes `--post-url` AND `--i-understand-staging-only`.

### Dry-run (recommended for runbook verification)

```bash
python -m v2.tools.signed_meta_webhook_smoke \
  --psid <ALLOWLISTED_PSID> \
  --text "smoke test"
```

You should see a block like:

```text
Signed Meta webhook smoke (dry-run output by default)
============================================================
  app_secret_env       : V2_STAGING_FB_APP_SECRET (configured, value not shown)
  signature_preview    : sha256=abcdef12...
  body_byte_length     : 248
  target_url           : http://localhost:5000/webhook
  mode                 : DRY-RUN (no network)
```

The signature value is intentionally truncated. The full digest is **not** logged.

### Actually POST against local / staging

Two-flag explicit opt-in is required:

```bash
python -m v2.tools.signed_meta_webhook_smoke \
  --psid <ALLOWLISTED_PSID> \
  --text "smoke test" \
  --post-url http://localhost:5000/webhook \
  --i-understand-staging-only
```

Expected:

- HTTP `200` with `{"status":"accepted","scheduled":1,"filtered":0}` when the PSID is on the allow-list.
- HTTP `200` with `{"status":"accepted","scheduled":0,"filtered":1}` when the PSID is NOT on the allow-list.
- HTTP `401 invalid signature` when the secret in the helper's env does not match `V2_STAGING_FB_APP_SECRET` on the server.

Hard rule: **never** pass the production Page webhook URL to `--post-url`. The helper has no way to know which URL you typed in - that gate is on the human.

---

## 6. Staging Meta Webhook Verification Steps

Once env vars are set and the helper smoke passes locally:

1. In the **Meta App Dashboard for the staging app**, set the Page webhook URL to `https://<v2-staging-url>/webhook` and the verify token to the value of `V2_STAGING_FB_VERIFY_TOKEN`.
2. Meta will send a `GET /webhook?hub.mode=subscribe&hub.verify_token=...&hub.challenge=...` request.
3. Expected response: status `200`, body equal to the challenge string. The V2 verifier returns `403 forbidden` if the verify token does not match.
4. Subscribe the staging app to the `messages` and `messaging_postbacks` page events (others are unnecessary for admin-only testing).
5. **Do not** subscribe production page events. Do not change the production page webhook.

---

## 7. Admin PSID Allowlist Setup and Verification

1. Collect each admin's PSID via the staging Messenger send-to-Page flow once. PSIDs are page-scoped - a Tiw PSID from the V1 page is NOT valid for the V2 staging app.
2. Populate `V2_STAGING_ADMIN_TEST_PSID_ALLOW_LIST` with **only** those PSIDs.
3. Re-run runtime-config check and verify `admin_test_psid_allow_list_count` matches the number of admins.
4. Confirm `admin_only_test_mode = enabled`.

> Tiw - please record the count expected for this run here before launching:
> _expected count: ____  date: ____  set-by: _____

---

## 8. Non-Allowlisted PSID Negative Test

Confirms the gate filters real customer traffic by accident.

Option A - pytest only (no network at all):

```bash
python -m pytest v2/tests/test_admin_only_runtime_smoke.py::TestAdminOnlyGate -q
```

The test `test_admin_only_enabled_allows_only_allowlisted_psid` exercises:

- A signed event from `PSID_ALLOWED` -> `scheduled: 1, filtered: 0`, conversation_turns row created.
- A signed event from `PSID_DENIED` -> `scheduled: 0, filtered: 1`, **no** rows created.

Option B - against staging using the helper (after step 6):

1. Pick a PSID that is intentionally NOT on the allow-list (e.g. a second staging-only test account).
2. Send a Messenger message from that account to the staging page.
3. Expected: `/webhook` returns `200` with `"scheduled":0, "filtered":1` in the body; no `conversation_turns` row appears in staging Supabase for that PSID; no outbound reply is delivered.

---

## 9. First 30-Minute Watch Checklist

Every 5 minutes for the first 30 minutes after enabling the staging webhook:

- [ ] Has any **non-admin PSID** been processed? Check `conversation_turns` for unexpected PSIDs. If yes -> disable webhook immediately (step 11).
- [ ] Has V2 sent **any outbound customer-facing reply**? It should never. If yes -> disable webhook immediately.
- [ ] Has any dashboard response shown a **raw PSID** or **raw token**? `_strip_raw_psid` should prevent this. If yes -> disable `/admin/*` routes.
- [ ] Has any **non-allow-listed LINE sender** mutated state via `/admin/line`? `LineAdminAdapter` should deny them silently. If yes -> disable `/admin/line`.
- [ ] Has any **user-typed post id** been accepted as `page_post_validated = true`? Only Meta-supplied refs validate against `page_posts`. If yes -> disable source-context planning.

Log timestamps for each check. After 30 minutes with all checks green, expand watch interval to 30 minutes for the next 4 hours.

---

## 10. Immediate Rollback / Disable Steps

Fastest safe disable, in priority order:

1. Set `V2_ADMIN_ONLY_TEST_MODE=false` in staging secrets, then restart the V2 service. The gate falls back to "admin_only_disabled" - but if you have already pointed the Meta webhook here, the next step is required.
2. In the Meta App Dashboard, **remove the webhook subscription** for the staging app, OR point the URL back to a known-safe endpoint. This stops Meta from delivering any further events to V2 within seconds.
3. If `V2_STAGING_DASHBOARD_TOKEN` may have been shared, **rotate it immediately** and update the staging secret store.
4. Clear `V2_STAGING_ADMIN_TEST_PSID_ALLOW_LIST`. With `V2_ADMIN_ONLY_TEST_MODE=true` and an empty list, the gate fails closed for every inbound.
5. Record the incident: when the watch tripped, which checklist item, which PSID class, which exact response/payload. Pass to Codex/QA for postmortem.

---

## 11. Not Approved Yet - Explicit Out-of-Scope List

This runbook does **NOT** approve any of the following. Each requires a separate task and explicit Tiw/Codex sign-off:

- Connecting V2 to the **production** Meta page webhook (the live customer page).
- Sending any **customer-facing outbound reply** from V2 (text, quick replies, attachments, or any DM).
- Live OpenAI / Anthropic / OCR / Document-parser / paid-provider calls.
- Reading or copying production customer data into V2 staging.
- Re-enabling Make.com scenario 4967547 (deactivated 2026-05-16).
- Applying any future Supabase migration from Claude Dev.
- Applying `_pending_023_departure_unique.sql.proposal` (still gated behind a clean duplicate audit on staging at run time, not just at audit time).
- Promoting V2 traffic to customer-wide (any traffic split or pilot beyond admin allow-list).

---

## Staging Data Readiness (controller-verified for DEV-016)

The following facts were verified by Codex against the V2 staging Supabase project `mbcihtcdwfofagkxphcu` before DEV-016 was opened. Do not re-apply or re-run these from Claude Dev.

- Migration `20260520_022_departure_refreshed_at.sql` applied to staging.
- `tour_departures.refreshed_at` column exists; index `idx_dep_refreshed_at` exists.
- `24/24` staging departure rows have `refreshed_at` populated.
- Duplicate audit (`v2.tools.departure_duplicate_audit.find_duplicates`) returned **zero** rows for the proposed UNIQUE-index key.
- `v2/supabase/migrations/_pending_023_departure_unique.sql.proposal` is **not applied** and **not in scope** for DEV-016.
- Applying the UNIQUE proposal still requires the audit to return zero rows **at apply time**, on staging, immediately before the migration runs. It is a Codex/operator action, not a Dev action.

If any of the above changes (rows added or modified, migration rolled back, audit not zero), **stop** before the next staging real-chat session and re-verify.

---

## Helper Index

- Preflight (env presence, no value echo): `python -m v2.tools.admin_only_preflight`
- Signed event smoke (offline dry-run by default): `python -m v2.tools.signed_meta_webhook_smoke`
- Duplicate audit (read-only): `python -m v2.tools.departure_duplicate_audit`
- Departure refresher (dry-run by default, no auto-wired clients): `python -m v2.tools.refresh_departure_rows`
- Live detail-page departure smoke (read-only HTTP to tourfiremai.com, no DB write): `python -m v2.tools.live_detail_departure_smoke`

All helpers are dry-run / read-only by default. None of them call Meta / LINE / OpenAI / OCR / paid providers.

---

**Last updated:** 2026-05-20 by DEV-2026-05-20-016 (Sprint 5 Package J).
**Next change trigger:** when Tiw/Codex approves V2 customer-facing outbound replies (separate task), OR when the production Meta webhook becomes in scope (separate task).
