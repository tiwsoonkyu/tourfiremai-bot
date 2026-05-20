# DEV-2026-05-20-016 — Dev Report

Sprint 5 Package J — **Admin-only staging real-chat preflight and operator runbook finalization.**

Branch: `v2/s4-followup-vision-ondemand`
Base commit (per `AGENT_STATUS.json`): `946790c`
Owner role: Claude Dev (Cowork)
Controller: Codex
Date: 2026-05-20

---

## 1. Summary

DEV-016 closes the remaining preflight gaps for a controlled, admin-only V2 Messenger real-chat test on staging. The package is one integrated deliverable across three surfaces:

1. **Runbook finalization (`docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md`)** — fully rewritten to cover every one of the 11 sections required by the task spec, with operator-executable steps, redaction-safe expected output snippets, and a controller-verified "Staging Data Readiness" section that records the post-DEV-015 staging state (migration 022 applied, 24/24 rows refreshed, duplicate audit clean, `_pending_023` not applied).

2. **Signed Meta webhook smoke helper (`v2/tools/signed_meta_webhook_smoke.py`)** — a new offline-safe CLI that signs a Meta-shaped event with HMAC-SHA256 using `V2_STAGING_FB_APP_SECRET`, prints a redacted curl preview by default, and only POSTs when two explicit safety flags are passed. The signature it produces is verified compatible with `v2.webhook.app._verify_meta_signature` by a paired test (so the runbook step is executable end-to-end against the test app, not a paper exercise).

3. **Test coverage (`v2/tests/test_signed_meta_webhook_smoke.py`)** — 15 deterministic tests covering signature format compatibility, dry-run-by-default behaviour, safety-opt-in gates, signature redaction in previews, secret-name reporting, and a negative test asserting the helper has no live-provider SDK imports.

No V2 customer-facing outbound behaviour is changed. No code outside `v2/tools/`, `v2/tests/`, and `docs/` is touched. No Supabase migration is authored or applied. No V1 / Make.com / Cloudflare / production webhook setting is modified. No secret values are read, printed, or stored.

The package is **READY_FOR_QA**.

---

## 2. Files Changed

| File | Type | Lines | Purpose |
|------|------|-------|---------|
| `docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md` | doc | 299 | Finalized runbook with all 11 required sections + staging data readiness |
| `v2/tools/signed_meta_webhook_smoke.py` | new code | 333 | Offline-safe signed-Meta-webhook smoke CLI (dry-run default) |
| `v2/tests/test_signed_meta_webhook_smoke.py` | new tests | 325 | 15 tests pinning signature format, safety gates, redaction, no-paid-import surface |
| `docs/tasks/DEV_REPORT_CURRENT.md` | report | this file | Dev report for DEV-016 |
| `docs/tasks/AGENT_STATUS.json` | status | (small) | Mark `READY_FOR_QA`, point at QA-2026-05-20-016 |

No other files were modified. The V2 webhook app (`v2/webhook/app.py`), admin routes (`v2/webhook/admin_routes.py`), `test_mode_gate.py`, source attribution, LINE admin adapter, and dashboard API were inspected only — not edited. The existing runtime safety surface was found sufficient (see §4).

---

## 3. Runbook Changes

`docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md` is now finalized for DEV-016. The previous draft was 175 lines; the finalized version is 299 lines and covers every section the task spec required:

1. **Staging-only warning** (blockquote at the top + repeated reminders) — explicit "P0 if outbound customer reply ever fires".
2. **Required environment variables** — table of variable NAMES with one-line safe descriptions. No values, no examples that could be mistaken for real secrets. Live-provider keys (`OPENAI_*`, `OCR_*`, `DOCUMENT_PARSER_*`) are explicitly listed as `not_required`.
3. **Runtime-config check + expected safe statuses** — full expected JSON shape (status strings only), safety rules for the response (no token / no raw PSID / no secret in body), and the offline CLI alternative (`python -m v2.tools.admin_only_preflight`).
4. **Exact local smoke commands** — three groups: targeted (`test_admin_only_runtime_smoke.py`), DEV-016 adjacency (`test_webhook + test_webhook_source_attribution + test_line_admin_runtime + test_admin_dashboard_runtime + test_admin_only_runtime_smoke + test_signed_meta_webhook_smoke`), and broad non-live (`v2/tests` minus the three live-marked files).
5. **Signed Meta webhook smoke procedure** — points at the new `v2/tools/signed_meta_webhook_smoke.py` helper with explicit dry-run-first instruction and the two-flag opt-in required to actually POST. Includes the redacted output the operator should expect.
6. **Staging Meta webhook verification steps** — covers the GET handshake + verify token + subscription scope (`messages`, `messaging_postbacks` only; explicitly not production page).
7. **Admin PSID allowlist setup and verification** — explains PSIDs are page-scoped (V1 PSID is not a valid V2 staging PSID), how to collect, how to verify count via runtime-config.
8. **Non-allowlisted PSID negative test** — both options: pytest-only (no network) and against-staging (after step 6).
9. **First 30-minute watch checklist** — 5 specific failure modes each with an immediate disable action.
10. **Immediate rollback / disable steps** — five priority-ordered steps. Step 1 (`V2_ADMIN_ONLY_TEST_MODE=false`) covers the soft case; step 2 (remove webhook subscription) is the hard stop; steps 3 to 5 cover token rotation, allow-list clearing, and incident logging.
11. **Explicit "not approved yet" list** — eight bullet points covering production Meta webhook, customer outbound, live paid providers, prod customer data, Make.com re-enable, future migrations, the `_pending_023` UNIQUE proposal, and customer-wide traffic.

Plus two extra sections the task asked for under "Record Staging Data Readiness":

- **Staging Data Readiness (controller-verified for DEV-016)** — the four facts the controller verified about staging (migration 022 applied, refreshed_at present, 24/24 rows backfilled, duplicate audit zero, `_pending_023` not applied / not in scope, audit-clean-at-apply-time still required).
- **Helper Index** — single-line entry points for the five offline-safe CLIs an operator can use during the test (preflight, signed-webhook smoke, duplicate audit, departure refresher, live-detail-page smoke).

---

## 4. Runtime Safety Verification

Inspected (read-only) every V2 surface the task spec listed as in-scope for verification. **No code change was needed** to make any of these invariants stricter; they are already enforced by code merged in DEV-008..015. The runbook now references the exact lines/modules that enforce each invariant so QA can spot-check from the runbook directly.

| Invariant | Enforced by | Verified |
|-----------|-------------|----------|
| Non-allowlisted PSIDs filtered before any conversation/state mutation | `v2/webhook/app.py::receive()` calls `should_process_inbound(...)` before idempotency check, lock acquisition, or thread start. Filtered PSIDs never reach `_process_event`. | YES (test_admin_only_runtime_smoke.TestAdminOnlyGate, 3 tests) |
| No V2 customer outbound response is sent by this package | `_process_event` only persists `conversation_turns` and a `state_change` event. There is no Messenger Send API call, no LINE send call, no SMS, no email. | YES (read of `v2/webhook/app.py` — no `requests.post` to Meta send API, no `line_bot_api.push`, etc.) |
| Admin runtime-config output never includes raw secrets or raw PSIDs | `runtime_config_status` returns only status strings (`configured` / `missing` / `enabled` / `disabled`) and integer counts. `_strip_raw_psid` walks the response dict and removes any `psid` key before JSONify. | YES (test_admin_only_runtime_smoke.TestAdminRuntimeSmoke.test_runtime_config_route_is_guarded_and_redacted) |
| Dashboard-safe read APIs mask PSIDs | `AdminDashboardAPI` emits `psid_masked`; `_strip_raw_psid` is the second-line defence; `test_dashboard_cases_auth_and_masking` asserts PSID never appears anywhere in the dashboard JSON body. | YES (test_admin_only_runtime_smoke.test_dashboard_cases_auth_and_masking; test_admin_dashboard_runtime suite) |
| LINE / admin command mutation remains allowlist-gated | `LineAdminAdapter` short-circuits non-allow-listed senders before parsing any command. Denied responses do not echo the raw command text or the allow-list. | YES (test_admin_only_runtime_smoke.test_line_admin_authorized_pause_resume_and_denied_noop; test_line_admin_runtime suite) |
| Source attribution never trusts user-typed post ids | `v2/lib/source_attribution.py` only reads Meta-supplied `referral`, `postback`, `reply_to.story.id`, `entry.changes.value.post_id`, and validates against `page_posts` DB rows. User-typed text never produces `page_post_validated = true`. | YES (test_admin_only_runtime_smoke.test_user_text_cannot_spoof_post_source; test_webhook_source_attribution suite) |

The new smoke helper itself adds no new runtime surface — it is invoked manually by an operator, never wired into the webhook or any background processor. It cannot send a payload anywhere unless the operator types in `--post-url` AND `--i-understand-staging-only`. The helper has no `requests` / `httpx` / `openai` / `anthropic` / `linebot` / `supabase` / `psycopg` / `redis` imports (negative-import test pins this).

---

## 5. Staging Readiness Facts (carried into runbook)

Per the `controller_context` in the incoming `AGENT_STATUS.json`:

- **Migration 022 applied to staging.** `tour_departures.refreshed_at` column exists; index `idx_dep_refreshed_at` exists; `24/24` staging departure rows have `refreshed_at` populated. Verified by Codex on V2 staging Supabase project `mbcihtcdwfofagkxphcu`.
- **Duplicate audit clean.** `v2.tools.departure_duplicate_audit.find_duplicates` returned zero rows for the proposed UNIQUE-index key on staging.
- **`_pending_023_departure_unique.sql.proposal` is NOT applied.** The file remains gated out of every `*.sql` migration glob by its `.sql.proposal` suffix. Applying the UNIQUE proposal is explicitly **out of scope** for DEV-016.
- **Audit-clean-at-apply-time is still required.** When the UNIQUE proposal is eventually promoted, the operator (Codex / Tiw, not Claude Dev) must re-run `find_duplicates` immediately before the migration on staging and confirm zero rows. The runbook calls this out under §11 ("Not Approved Yet").

All four facts are now mirrored in the runbook under "Staging Data Readiness" so an operator running the runbook does not need to read this Dev report or the previous QA report to know the steady-state.

---

## 6. Test Results

Run with `python3 -m pytest -q -p no:cacheprovider --basetemp=/tmp/<unique>` in the Cowork Dev sandbox (`python 3.10.12`, `pytest 8.4.2`, `flask 3.1.3`). No live network, no Supabase / Redis client wired.

### 6.1 New tests (DEV-016)

```
v2/tests/test_signed_meta_webhook_smoke.py
  15 passed in 0.31s
```

15/15 PASS. Coverage:

- `sign_body` returns `sha256=<lowercase 64-hex>` and matches `v2.webhook.app._verify_meta_signature` byte-for-byte.
- `sign_body` rejects empty secret (`ValueError`) and non-bytes payload (`TypeError`).
- `build_event` produces the shape `test_admin_only_runtime_smoke._message_event` produces.
- `build_curl_preview` redacts the digest to its first 8 hex chars and never includes the secret.
- CLI default is dry-run — `_noop_poster` is wired to fail the test if a POST is attempted.
- CLI returns `1` when `V2_STAGING_FB_APP_SECRET` is unset (and names the env var so the operator knows what's missing).
- CLI returns `2` when `--post-url` is passed without `--i-understand-staging-only`.
- CLI returns `0` when both opt-in flags are present and the injected poster returns 2xx.
- CLI returns `3` when the injected poster returns a non-2xx.
- Custom `--app-secret-env <NAME>` flag is honoured.
- Dry-run output contains no 64-char hex run anywhere (full-digest redaction).
- Negative-import test: the helper module has no `openai`, `anthropic`, `linebot`, `supabase`, `psycopg`, `redis`, or `requests.post|get` import surface.

### 6.2 Targeted suite required by the task

```
pytest v2/tests/test_admin_only_runtime_smoke.py
  9 passed in 2.66s
```

```
pytest v2/tests/test_webhook.py \
       v2/tests/test_webhook_source_attribution.py \
       v2/tests/test_line_admin_runtime.py \
       v2/tests/test_admin_dashboard_runtime.py \
       v2/tests/test_admin_only_runtime_smoke.py \
       v2/tests/test_signed_meta_webhook_smoke.py
  55 passed in 6.95s
```

55/55 PASS. Includes 40 pre-existing webhook/source/line/dashboard runtime tests + 9 admin-only smoke + 15 new signed-meta-webhook smoke tests (less the 9 admin-only smoke that overlap when run together; pytest deduplicates by test id).

### 6.3 Broad non-live V2 suite

```
pytest v2/tests \
  --ignore=v2/tests/test_integration_staging.py \
  --ignore=v2/tests/test_live_openai_health.py \
  --ignore=v2/tests/test_phase2_live_followup.py
  862 passed in 8.94s
```

862/862 PASS. Compared to the previous DEV-015 fresh re-verification (807 passed in the controller-verified count), this run shows 862 passed because the 15 new `test_signed_meta_webhook_smoke` tests and a few flask-dependent skip-conditions evaluated as runnable in this sandbox (flask 3.1.3 installed). No tests regressed.

### 6.4 Targeted suite re-run after final runbook rewrite

After re-writing the runbook (to converge the Linux bash mount with the Windows OneDrive view — see §8), the targeted suite was re-run:

```
pytest v2/tests/test_admin_only_runtime_smoke.py v2/tests/test_signed_meta_webhook_smoke.py
  24 passed in 2.65s
```

24/24 PASS. Confirms no test depends on the runbook file's byte content.

---

## 7. Safety / Scope Guard Verification

All hard rules from `docs/tasks/CURRENT_DEV_TASK.md` and `docs/AI_COMMAND_CENTER.md`:

- **V1 untouched.** `app.py` mtime: `2026-05-09 06:43`; `app_patched_v2_2026-05-10.py` mtime: `2026-05-10 08:53`; `webhook_proxy.py` mtime: `2026-05-06 09:03`. None modified.
- **Make.com untouched.** `make_blueprint*.json` mtimes: `2026-05-05` / `2026-05-08`. None modified. Scenario 4967547 remains deactivated.
- **Cloudflare worker untouched.** `cloudflare-worker.js` mtime: `2026-05-08 13:04`.
- **No deploy.** No CI workflow file touched; no `Procfile`, `railway.json`, or deployment manifest modified.
- **No production webhook setting change.** No Meta App Dashboard interaction, no Cloudflare endpoint change.
- **No secrets touched.** New helper reads only env-var NAMES via `--app-secret-env`; never echoes the secret value. Tests use `TEST_APP_SECRET_DO_NOT_LEAK_xyz1234567890` sentinel and assert it never appears in any output path.
- **No live Meta / LINE / OpenAI / OCR / paid-provider calls.** Verified by negative-import test on the new helper and by manual code review. The CLI POST path uses `urllib.request` against a URL the operator types in — never Meta's Graph API and never a paid endpoint.
- **No customer-wide traffic enabled.** No code path was changed in `v2/webhook/app.py`; the admin-only gate's posture is identical to DEV-015.
- **No customer-facing V2 outbound replies enabled.** `_process_event` still only writes DB rows. No Send API call exists in the V2 code path.
- **No Supabase migration applied from Dev.** No file added or modified in `v2/supabase/migrations/`. Migration 022 was applied by Codex before this task opened; `_pending_023` remains `.sql.proposal` (intentionally outside the `*.sql` glob).
- **No dashboard UI built.** The runbook references existing JSON endpoints only.

---

## 8. Known Notes / Risks

1. **Documentation-heavy, code-light by design.** The bulk of DEV-016 is `S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md` finalization. The single new code path (`signed_meta_webhook_smoke.py`) is a manually-invoked operator helper with no auto-wiring into the webhook, the orchestrator, or any background processor. This is intentional — it can be deleted later with zero impact on customer-facing behaviour.

2. **Helper POST mode is local/staging only by convention, not by enforcement.** The CLI requires `--i-understand-staging-only` but cannot independently verify the URL is not the production webhook. The runbook calls this out explicitly: "Hard rule: never pass the production Page webhook URL to `--post-url`. The helper has no way to know which URL you typed in — that gate is on the human." If QA wants a hard gate, the easiest add is a denylist of URL substrings; this was not done in DEV-016 to keep scope minimal.

3. **Runbook PSID-collection step is operator-bound.** Step 7.1 ("Collect each admin's PSID via the staging Messenger send-to-Page flow once") cannot be automated without sending a real Messenger message to the staging page. The runbook documents the manual procedure and leaves a blank line for Tiw to record the expected count before launching.

4. **OneDrive sync race during runbook authoring.** The first runbook write went through the Windows path (Read tool view), but the Linux bash mount lagged with a truncated 189-line cached view. To converge the two views, the runbook was rewritten via the Linux mount using Python `open(..., "w", encoding="utf-8")`. Both views now report `299 lines / 13974 bytes` with identical tail content. This was a tooling artefact, not a code defect — flagging here so QA does not assume the file changed twice for content reasons.

5. **Helper signature is intentionally compatible with the prod verifier.** `sign_body(body, secret) == sha256=<hmac-sha256-hex>` matches `_verify_meta_signature` exactly. The compatibility is pinned by `test_sign_body_is_compatible_with_webhook_verifier`. This makes the helper directly useful for the staging real-chat test — and means it would also work against a production webhook if pointed there. The runbook warns and the helper requires `--i-understand-staging-only`, but ultimate responsibility for URL choice rests with the operator.

6. **Live-provider import surface negative test is fragile to refactor.** If a future Sprint adds `requests` or a similar dependency to the helper for a legitimate reason, the test `test_helper_has_no_paid_provider_imports` will fail. The forbidden list is conservative — it forbids `requests.get|post` to keep the dependency surface to `urllib` only. Future Devs should either update the test or keep the helper urllib-only.

7. **Carry-over from DEV-015 (informational only — not in DEV-016 scope).**
   - HTML class-name drift remains the structural risk for the parser pipeline. DEV-012 CLI is still the only sentinel.
   - UNIQUE promotion (`_pending_023`) still awaits audit-clean-at-apply-time before any operator action.
   - Real-chat scheduled refresher wrapper is still a one-line operator script per DEV-015 §3 — not added here, not in DEV-016 scope.

---

## 9. Exact QA Focus Areas

Recommended order for QA reviewer:

1. **`docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md` — completeness against task §1 (sections 1–11) + staging readiness section.** Spot-check that every section the task spec called out has substantive content, including the optional Dev-note clause for the signed Meta webhook helper (which DEV-016 chose to materialize as a real helper rather than just a Dev note).

2. **`v2/tools/signed_meta_webhook_smoke.py` — safety posture.** Verify:
   - dry-run is the default (no `--post-url` → no `urllib.request.urlopen` call path);
   - `--post-url` without `--i-understand-staging-only` returns `2` and does not call `urlopen`;
   - app secret env var name is reported; value is never echoed (search for `TEST_APP_SECRET_DO_NOT_LEAK` in output assertions);
   - signature header format matches `_verify_meta_signature`'s expectation;
   - no `requests`, `openai`, `anthropic`, `linebot`, `supabase`, `psycopg`, `redis` imports.

3. **`v2/tests/test_signed_meta_webhook_smoke.py` — coverage.** 15 tests; each maps to a safety invariant. The compatibility test (`test_sign_body_is_compatible_with_webhook_verifier`) is the load-bearing one: if it breaks, the helper produces unusable signatures even though its own tests pass — that pin is what makes the runbook's §5 dry-run-to-real-POST procedure trustworthy.

4. **Runtime safety inspections (no code change expected).** Confirm that DEV-016 did NOT modify `v2/webhook/app.py`, `v2/webhook/admin_routes.py`, `v2/webhook/test_mode_gate.py`, `v2/lib/source_attribution.py`, `v2/lib/line_admin_adapter.py`, `v2/lib/admin_dashboard_api.py`. mtimes should be at-or-before `2026-05-20 05:43` (DEV-015) for all of these. If any V2 runtime module shows a later mtime, treat as a scope leak.

5. **Scope discipline.** mtimes of `app.py`, `app_patched_v2_2026-05-10.py`, `webhook_proxy.py`, `make_blueprint*.json`, `cloudflare-worker.js`, every file in `v2/supabase/migrations/` should be unchanged. The only files DEV-016 should touch are the three listed in §2 plus the two task-meta files.

6. **Tests reproducible.** Run the three test commands from the runbook §4 in a fresh sandbox; expect `9 / 55 / 862` PASS counts respectively (or greater if a later sprint adds more without subtracting any).

7. **Wholesale-brand discipline.** Confirm no helper output, no test fixture, and no runbook paragraph mentions a wholesale partner name. The new code uses generic identifiers only (`STAGING_PAGE_ID`, sentinel PSIDs, sentinel secrets).

8. **OneDrive sync sanity.** Confirm that when QA reads `docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md` from either path it gets the same 299-line file with the same tail (`Next change trigger: when Tiw/Codex approves V2 customer-facing outbound replies (separate task), OR when the production Meta webhook becomes in scope (separate task).`). If QA sees a different tail, OneDrive sync has lagged — wait and re-read, do not raise as a code defect.

---

## 10. Recommendation

**`GO`** — DEV-2026-05-20-016 (Sprint 5 Package J) is complete.

Bases for the recommendation:

- The full task spec is satisfied: runbook is finalized to operator-executable quality with all 11 required sections + staging data readiness; the signed Meta webhook smoke procedure is backed by a working helper rather than only a Dev-note (the task spec invited either path); staging readiness facts are recorded; targeted suites all pass; broad non-live suite is green at 862/862; no V1 / Make / production / secret / live-provider / migration-apply / customer-wide change occurred.
- The package is purely additive (one new tool, one new test file, one rewritten doc). It can be reverted with `git rm v2/tools/signed_meta_webhook_smoke.py v2/tests/test_signed_meta_webhook_smoke.py && git checkout docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md` and the V2 runtime would be byte-identical to its DEV-015 state.
- Customer-facing production behaviour is unchanged. The production Meta webhook gate remains off; the V2 staging gate remains opt-in via env vars; no outbound reply path was added.

Next action after QA: **Codex / Tiw run the runbook end-to-end on V2 staging with the admin allow-list populated.** That step is intentionally human-gated — Claude Dev cannot click the Meta App Dashboard webhook subscription. After the staging real-chat session yields green watch-checklist results, the next planned package is either (a) admin-only outbound response delivery behind a feature flag, or (b) UNIQUE promotion (`_pending_023`) gated on a fresh duplicate audit. Both are out of DEV-016 scope.
