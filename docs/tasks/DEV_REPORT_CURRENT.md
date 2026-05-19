# DEV REPORT - DEV-2026-05-19-010

## 1. Status

READY_FOR_QA

Dev recommendation: GO

Branch: `v2/s4-followup-vision-ondemand`

Task: Sprint 5 Package D - Admin-Only Real Chat Readiness, Runtime Smoke Tests, and Operator Runbook

## 2. Scope Implemented

This task makes the V2 staging webhook safer to test with real Messenger traffic from admin accounts only.

Implemented:

1. Admin-only inbound gate for `POST /webhook`.
2. Safe runtime config/readiness endpoint for admin-only testing.
3. Runtime smoke tests covering admin-only ingestion, source attribution, dashboard auth/masking, and LINE admin pause/resume.
4. Operator runbook for the first admin-only real chat test.

No production webhook was changed. No V1 code was touched. No live Meta, LINE, OpenAI, OCR, or paid-provider calls were made.

## 3. Files Changed

Runtime:

- `v2/webhook/app.py`
- `v2/webhook/admin_routes.py`
- `v2/webhook/test_mode_gate.py` (new)

Tests:

- `v2/tests/test_admin_only_runtime_smoke.py` (new)

Docs:

- `docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md` (new)
- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

## 4. Admin-Only Safety Gate

New helper module: `v2/webhook/test_mode_gate.py`

Key behavior:

- If `V2_ADMIN_ONLY_TEST_MODE` is disabled, existing webhook ingestion behavior is preserved.
- If `V2_ADMIN_ONLY_TEST_MODE` is enabled and no admin PSID allow-list is configured, the webhook fails closed and filters all inbound events.
- If enabled with an allow-list, only matching PSIDs are scheduled for processing.
- Filtered events return HTTP 200 to Meta but are not scheduled, avoiding retries while preventing bot replies to non-admin customers.
- Raw PSIDs are never returned in runtime readiness output.

Webhook response now includes:

- `scheduled`
- `filtered`

This gives operators a quick signal during staging tests without exposing customer identifiers.

## 5. Runtime Config Validator

New guarded endpoint:

`GET /admin/runtime-config`

Auth:

- Requires existing `X-Admin-Token` header.

Returned values are statuses/counts only:

- admin-only mode enabled/disabled
- admin test PSID allow-list status/count
- dashboard token configured/missing
- LINE admin allow-list configured/missing
- Supabase staging URL configured/missing
- FB app secret configured/missing
- FB verify token configured/missing

The endpoint does not echo tokens, secrets, raw PSIDs, or raw allow-list values.

## 6. Runtime Smoke Coverage

New test file: `v2/tests/test_admin_only_runtime_smoke.py`

Coverage:

1. Admin-only disabled still allows normal webhook ingestion.
2. Admin-only enabled allows only allowlisted PSID.
3. Admin-only enabled without PSID allow-list fails closed.
4. Meta post referral records validated page post source.
5. User text cannot spoof page post source.
6. Runtime config endpoint is guarded and redacted.
7. Runtime config marks empty LINE admin allow-list as missing.
8. Dashboard cases endpoint requires auth and masks PSIDs.
9. LINE admin pause/resume works only for allowed admin sender and does not echo raw PSIDs.

All tests use in-memory fakes. They do not call Meta, LINE, OpenAI, OCR providers, Supabase, Redis, or any production endpoint.

## 7. Tests Run

Targeted smoke:

```bash
.\.venv_codex\Scripts\python.exe -m pytest v2/tests/test_admin_only_runtime_smoke.py -q
```

Result:

```text
9 passed
```

Targeted runtime package:

```bash
.\.venv_codex\Scripts\python.exe -m pytest v2/tests/test_webhook.py v2/tests/test_webhook_source_attribution.py v2/tests/test_line_admin_runtime.py v2/tests/test_admin_dashboard_runtime.py v2/tests/test_admin_only_runtime_smoke.py -q -p no:cacheprovider --basetemp=.pytest_tmp_target
```

Result:

```text
40 passed
```

Broad non-live V2 suite:

```bash
.\.venv_codex\Scripts\python.exe -m pytest v2/tests --ignore=v2/tests/test_integration_staging.py --ignore=v2/tests/test_live_openai_health.py --ignore=v2/tests/test_phase2_live_followup.py -q -p no:cacheprovider --basetemp=.pytest_tmp
```

Result:

```text
671 passed
```

Note: the first broad-suite attempt without `--basetemp=.pytest_tmp` hit a Windows temp permission issue under `AppData\Local\Temp\pytest-of-supak`. The repo-local basetemp rerun passed cleanly and is the canonical command for this Windows workspace.

## 8. Safety Checks

Confirmed:

- No V1 files changed.
- No Make.com changes.
- No Cloudflare changes.
- No Railway deploy.
- No production Meta webhook change.
- No secrets added.
- No live external provider calls.
- No migrations applied in this task.
- New readiness endpoint is admin-token guarded.
- Admin-only test mode fails closed when allow-list is missing.
- PSIDs are masked/redacted in runtime outputs.

## 9. Known Gaps / Next Recommended Action

Not implemented in this task:

- Real Meta staging webhook deployment.
- Real LINE delivery.
- Real dashboard frontend.
- Production customer rollout.

Recommended next step:

QA should review this package as an integration unit. If QA returns GO, the controller can prepare a controlled admin-only staging test:

1. Set staging env vars.
2. Verify `/admin/runtime-config`.
3. Route only admin PSID traffic.
4. Test one real admin Messenger conversation.
5. Watch `scheduled` vs `filtered` counts and dashboard/LINE admin controls.

Stop condition if any issue appears:

- Disable `V2_ADMIN_ONLY_TEST_MODE` or remove the staging webhook route before allowing real customer traffic.
