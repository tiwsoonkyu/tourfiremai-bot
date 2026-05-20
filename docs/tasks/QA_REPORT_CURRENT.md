# QA Report — QA-2026-05-20-016

## 1. Verdict

`GO_WITH_NOTES`

Reviewer: Claude QA (Cowork session, 2026-05-20)
Dev task reviewed: `DEV-2026-05-20-016` (Sprint 5 Package J —
Admin-Only Staging Real-Chat Preflight and Operator Runbook
Finalization)
Branch: `v2/s4-followup-vision-ondemand`
Base commit (per Dev / AGENT_STATUS.json): `946790c`

Verdict means: the package is complete and operator-ready. All 21
charter checks (A–D) pass. The runbook is executable end-to-end with
no guessing required, the signed Meta webhook smoke helper is safe-by-
default (dry-run + two-flag opt-in to POST, secret value never echoed,
no live-provider import surface), and the V2 runtime safety surface is
verifiably unchanged (every runtime module mtime predates this task).
Customer-facing production behaviour does not change with this merge.

Notes are minor and consist of:
- one new low-severity operator-convention item (POST URL is not
  enforced to be staging — Dev explicitly acknowledges and runbook
  warns);
- one P3 documentation formatting nit (section 1 delivered as a
  blockquote rather than a numbered heading);
- carry-over items from earlier sprints (UNIQUE promotion still
  gated, scheduled-refresher wrapper still operator-script).

This verdict does **not** approve:

- pointing the production Meta webhook at the V2 staging URL,
- enabling customer-facing V2 outbound replies,
- live LLM / OCR / paid-provider calls,
- applying any migration (including the `_pending_023` UNIQUE
  proposal),
- production go-live.

## 2. Scope Reviewed

Reviewed `DEV-2026-05-20-016` as one integrated preflight package
against the charter sections A–D in `docs/tasks/CURRENT_QA_TASK.md`:

- **A. Scope discipline:** no V1, Make.com, Cloudflare, production
  webhook, secrets, live providers, migration apply, customer-wide
  traffic, or customer-facing V2 outbound reply.
- **B. Admin-only runtime safety:** non-allowlisted PSID filter
  before any conversation/state mutation, allowlisted PSIDs pass the
  gate, runtime-config returns only safe statuses with no raw
  secrets/PSIDs, dashboard masks PSIDs, LINE/admin mutation
  allowlist-gated, source attribution does not trust user-typed post
  IDs.
- **C. Runbook quality:** executable by operator without guessing,
  contains env checklist + runtime check + smoke tests + PSID
  allowlist setup + negative test + watch checklist + rollback, says
  V2 is not approved for production webhook/customer outbound, records
  migration 022 applied + duplicate audit zero, says UNIQUE proposal
  is not applied.
- **D. Tests:** targeted suite passes, broad non-live V2 suite
  passes.

Artefacts inspected:

| File | Type | Size | mtime |
|------|------|-----:|-------|
| `docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md` | doc rewrite | 13,974 | 2026-05-20 10:56 |
| `v2/tools/signed_meta_webhook_smoke.py` | new | 11,879 | 2026-05-20 10:50 |
| `v2/tests/test_signed_meta_webhook_smoke.py` | new | 10,503 | 2026-05-20 10:51 |
| `docs/tasks/DEV_REPORT_CURRENT.md` | DEV-016 report | — | 2026-05-20 |
| `docs/tasks/AGENT_STATUS.json` | Dev snapshot | — | 2026-05-20 18:00 |

Out-of-scope artefacts (verified untouched by mtime inspection):

- **V1**: `app.py` mtime 2026-05-09; `webhook_proxy.py` mtime
  2026-05-06.
- **Make.com**: newest `make_blueprint*.json` mtime 2026-05-08.
- **Cloudflare worker**: `cloudflare-worker.js` mtime 2026-05-08.
- **V2 runtime modules (all predate DEV-016 work)**:
  - `v2/webhook/app.py` mtime 2026-05-19 15:55
  - `v2/webhook/admin_routes.py` mtime 2026-05-19 15:55
  - `v2/webhook/test_mode_gate.py` mtime 2026-05-19 16:06
  - `v2/lib/source_attribution.py` mtime 2026-05-19 14:19
  - `v2/lib/line_admin_adapter.py` mtime 2026-05-19 14:19
  - `v2/lib/admin_dashboard_api.py` mtime 2026-05-19 14:19
- **Supabase migrations**: latest is
  `20260520_022_departure_refreshed_at.sql` mtime 2026-05-19 18:51
  (from DEV-015 — Codex applied to staging). No new migration in
  DEV-016. `_pending_023_departure_unique.sql.proposal` correctly
  remains outside the `*.sql` glob.

## 3. Test Results

QA re-executed in the Cowork workspace (no network, no live providers,
no secrets, no migration apply, `flask 3.1.x` installed in the QA
sandbox).

### 3.1 Targeted DEV-016 helper + admin-only runtime

```text
PYTHONPATH=. python3 -m pytest \
  v2/tests/test_signed_meta_webhook_smoke.py \
  v2/tests/test_admin_only_runtime_smoke.py \
  --basetemp=/tmp/pyt_qa16c -p no:cacheprovider
=> 24 passed in 2.59s
```

Matches Dev report §6 exactly (15 new + 9 admin-only smoke = 24).

### 3.2 Adjacency suite required by the task

```text
PYTHONPATH=. python3 -m pytest \
  v2/tests/test_webhook.py \
  v2/tests/test_webhook_source_attribution.py \
  v2/tests/test_line_admin_runtime.py \
  v2/tests/test_admin_dashboard_runtime.py \
  v2/tests/test_admin_only_runtime_smoke.py \
  v2/tests/test_signed_meta_webhook_smoke.py \
  --basetemp=/tmp/pyt_qa16d -p no:cacheprovider
=> 55 passed in 6.74s
```

Matches Dev report §6.2 exactly.

### 3.3 Broad non-live V2 suite

```text
PYTHONPATH=. python3 -m pytest v2/tests \
  --ignore=v2/tests/test_integration_staging.py \
  --ignore=v2/tests/test_live_openai_health.py \
  --ignore=v2/tests/test_phase2_live_followup.py \
  --basetemp=/tmp/pyt_qa16_broad -p no:cacheprovider
=> 862 passed in 8.26s
```

Matches Dev report §6.3 exactly. Compared to DEV-015's 807 passed +
40 skipped baseline, this run shows 862 passed / 0 skipped because
flask is installed in the QA sandbox (DEV-016 added 15 tests + flask
unlocks 40 previously-skipped webhook tests = +55 passed).

### 3.4 Flask gating observation

QA's first attempt to run the DEV-016 helper test suite without flask
installed exhibited two failures
(`test_sign_body_is_compatible_with_webhook_verifier` and
`test_main_post_url_with_opt_in_invokes_injected_poster`) because both
import `v2.webhook.app._verify_meta_signature`, which loads `flask` at
module-import time. This is the **same environmental contingency**
Dev report §6 explicitly notes (flask 3.1.3 in Dev's sandbox). Once
flask is installed, all 15 tests pass. **Not a code defect.** Worth
noting in the runbook so operators on a fresh clone install flask
before running the helper's test suite.

### 3.5 Test-class breakdown of the 15 new helper tests

Mapped against the safety properties they pin:

| Test | Property |
|------|----------|
| signature format = `sha256=<64-hex>` | charter B — webhook signature shape |
| signature byte-compatible with `_verify_meta_signature` | runbook §5 — load-bearing dry-run-to-real-POST chain |
| `sign_body` rejects empty secret / non-bytes payload | safety |
| `build_event` shape matches admin-only runtime smoke fixture | runbook §5 — operator confidence |
| `build_curl_preview` redacts digest to first 8 hex chars | secret redaction |
| dry-run is default | helper safety posture |
| missing env var → exit 1 + names the var (no value) | secret never echoed |
| `--post-url` without `--i-understand-staging-only` → exit 2 | two-flag opt-in |
| both opt-in flags + injected poster 2xx → exit 0 | success path |
| both opt-in flags + injected poster non-2xx → exit 3 | failure surfaces |
| `--app-secret-env <NAME>` honoured | flexibility |
| dry-run output has no 64-char hex run | full-digest redaction |
| helper has no `openai`/`anthropic`/`linebot`/`supabase`/`psycopg`/`redis`/`requests.get|post` imports | no paid-provider import surface |

## 4. Findings, ordered by severity

### P0 — Blocking
None.

### P1 — Must fix before customer-facing wiring (not blocking this QA)
None.

### P2 — Should fix in the next package

- **P2-1 (new, low):** Helper POST mode is local/staging-only by
  *convention*, not by *enforcement*. The CLI requires
  `--i-understand-staging-only` but cannot independently verify the
  URL is not the production webhook. Dev report §8.2 calls this out
  explicitly and the runbook warns. Easy add to close: a URL
  substring denylist (e.g. reject any URL containing the production
  Page ID or `cloudflare-worker` path). Not done in DEV-016 to keep
  scope minimal — flag for the next operator-tooling task.

- **P2-2 (carried over from QA-013/QA-014/QA-015):**
  `idx_dep_full_row` UNIQUE promotion still gated. Migration 022 is
  applied, duplicate audit is clean per controller verification, but
  `_pending_023_departure_unique.sql.proposal` remains correctly
  outside the `*.sql` glob and is explicitly out of DEV-016 scope.
  The runbook §11 lists this as "Not Approved Yet" and §"Staging
  Data Readiness" notes that audit-clean-at-apply-time is still
  required when the proposal is eventually promoted. Closure
  requires Codex/Tiw to (a) re-run `find_duplicates` immediately
  before the apply, (b) rename the proposal to a real `.sql`
  migration, and (c) apply via the standard staging pipeline.

- **P2-3 (carried over from QA-015):** Scheduled refresher operator
  wrapper documentation. DEV-016 references the refresher in the
  runbook's Helper Index but does not document the wrapper-script
  pattern. The CLI's intentional no-op safety net continues to
  require an operator wrapper. Worth folding into the runbook in a
  future package.

### P3 — Nits

- **P3-1:** Runbook section 1 ("Staging-only warning") is delivered
  as a blockquote at the top (`> 1. Staging-only warning ...`) rather
  than as a numbered `## 1. Staging-Only Warning` heading. Content
  satisfies the charter (warning is present, prominent, and
  references the P0 incident posture). Future revision could
  promote it to a real numbered heading for parser-friendliness.

- **P3-2:** `test_helper_has_no_paid_provider_imports` is a
  conservative negative-import test that forbids `requests.get|post`
  to keep the dependency surface to `urllib` only. Dev report §8.6
  warns that legitimate future additions of `requests` would require
  updating this test. Acceptable trade-off; consider a `# noqa:
  imports-allowlist` annotation if `requests` is ever introduced.

- **P3-3 (carried over from QA-013):** Three pre-existing tests fail
  when pytest is invoked from `v2/` instead of repo root
  (`test_admin_only_preflight`, two `TestNoSecretOrWholesaleLeakage`).
  Hygiene fix to `pathlib.Path(__file__).resolve().parents[...]`.
  Not introduced by this task.

- **P3-4 (new, informational):** When running the DEV-016 helper test
  suite on a fresh clone without flask installed, two tests fail with
  `ModuleNotFoundError: No module named 'flask'` because the
  compatibility test imports `v2.webhook.app`. Dev report mentions
  flask 3.1.3 as a sandbox prerequisite in §6 prose but the runbook
  §4 smoke commands do not explicitly say "ensure flask installed
  first". Consider adding a `pip install flask` line to the runbook's
  prerequisite section.

### Charter / process

- **CTR-1:** `TASK_LOG.md` still ends at `QA-2026-05-20-015`. Codex
  should append entries for `DEV-2026-05-20-016` (accepted) and
  `QA-2026-05-20-016` (`GO_WITH_NOTES`) when committing this
  artefact.

## 5. Required Fixes

None for this cycle. All P2 items are deferred-by-design or
operator-tooling debt; P3 items are nits.

Recommended next-package follow-ups (informational):

1. Add a URL substring denylist to the signed-webhook helper to make
   "never point at production" a hard gate rather than a convention
   (closes P2-1).
2. Promote the runbook's blockquote staging-only warning to a
   numbered `## 1.` heading (closes P3-1).
3. Add a `pip install flask` prerequisite line to the runbook smoke
   commands section (closes P3-4).
4. After Tiw runs the real-chat staging session and the 30-minute
   watch checklist returns green, open the next planned task: either
   admin-only outbound response delivery behind a feature flag, or
   the gated UNIQUE promotion (`_pending_023`).

## 6. Notes / Residual Risks

1. **All six runtime safety invariants verified by inspection.** Per
   Dev report §4 and confirmed by QA's mtime check:
   - `v2/webhook/app.py` (admin-only PSID filter before any state
     mutation): mtime 2026-05-19 15:55 — unchanged in DEV-016.
   - `v2/webhook/admin_routes.py` (runtime-config + dashboard read API
     mask PSIDs/secrets): mtime 2026-05-19 15:55 — unchanged.
   - `v2/webhook/test_mode_gate.py` (PSID allowlist): mtime
     2026-05-19 16:06 — unchanged.
   - `v2/lib/source_attribution.py` (Meta-only fields, validates
     against `page_posts`): mtime 2026-05-19 14:19 — unchanged.
   - `v2/lib/line_admin_adapter.py` (allowlist-gated mutation): mtime
     2026-05-19 14:19 — unchanged.
   - `v2/lib/admin_dashboard_api.py` (PSID masking): mtime 2026-05-19
     14:19 — unchanged.

2. **Helper safety posture confirmed by structural inspection.**
   - Only third-party deps: `urllib.request` (stdlib), `hmac`
     (stdlib), `hashlib` (stdlib), `json`, `os`, `shlex`. No
     `requests`, `openai`, `anthropic`, `linebot`, `supabase`,
     `psycopg`, `redis` — verified by `grep` over the source.
   - Secret env var name only via `--app-secret-env` (default
     `V2_STAGING_FB_APP_SECRET`); value read once via `env.get(name)`
     and never put back into stdout/stderr. `_redact_signature`
     truncates the digest to 8 hex chars + `...` for the curl preview.
   - Dry-run is the default. `--post-url` alone returns exit 2.
     `--post-url --i-understand-staging-only` is the explicit opt-in;
     `_do_post` uses `urllib.request.urlopen` with the operator-typed
     URL only.
   - Exit codes: 0 success, 1 missing secret env, 2 missing opt-in,
     3 non-2xx response from injected poster.

3. **Page-post / sold-out block + FEE_CHECK_REQUIRED policy
   unchanged.** No runtime module was modified by this task; the
   DEV-014 invariants (page-post canned reply wins before LLM;
   `FEE_CHECK_REQUIRED` still uses `decide_fee_answer` +
   `CANNED_HANDOFF_FEE_INCOMPLETE`) continue to hold structurally.

4. **Staging data readiness recorded.** Runbook section "Staging
   Data Readiness" mirrors the four controller-verified facts from
   Codex's pre-task application of migration 022:
   - `tour_departures.refreshed_at` column present;
   - `idx_dep_refreshed_at` index present;
   - 24/24 staging departure rows have `refreshed_at`;
   - duplicate audit returned zero rows;
   - `_pending_023_departure_unique.sql.proposal` not applied (and
     not in DEV-016 scope);
   - audit-clean-at-apply-time still required for any future UNIQUE
     promotion.

5. **OneDrive sync race documented.** Dev report §8.4 flags that
   the runbook was rewritten via the Linux mount to converge two
   diverged views; both views now report `299 lines / 13,974 bytes`
   with identical tail. QA confirmed line count (299) and tail
   content matches Dev report (`Next change trigger: when Tiw/Codex
   approves V2 customer-facing outbound replies (separate task), OR
   when the production Meta webhook becomes in scope (separate
   task).`).

6. **Hard-rule compliance verified.** Grep over the new helper +
   test file confirms no forbidden imports
   (`openai`/`anthropic`/`requests`/`psycopg`/`supabase`/`httpx`/
   `boto3`/`linebot`/`redis`). No secret/token reads beyond the
   declared env-var name lookup. No wholesale partner names in the
   helper, the test file, or the runbook (`grep -i wholesale` returns
   zero matches across all three).

7. **Flask is required in the test sandbox** for the helper's
   compatibility test
   (`test_sign_body_is_compatible_with_webhook_verifier`) to run —
   that test imports `v2.webhook.app._verify_meta_signature`, which
   loads flask at module import. Dev's sandbox had flask 3.1.3
   installed; QA installed it before re-running. This is consistent
   with Dev's broad-suite count of 862 (which includes the
   previously-skipped flask-only webhook tests once flask is
   present).

## 7. Recommendation to Codex

1. **Accept `QA-2026-05-20-016` as `GO_WITH_NOTES`.** Commit the
   three artefacts (`docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md`,
   `v2/tools/signed_meta_webhook_smoke.py`,
   `v2/tests/test_signed_meta_webhook_smoke.py`) plus this QA report
   + updated `AGENT_STATUS.json` from a local clone on
   `v2/s4-followup-vision-ondemand`. Append `TASK_LOG.md` entries
   for `DEV-2026-05-20-016` (accepted) and `QA-2026-05-20-016`
   (`GO_WITH_NOTES`).

2. **Run the runbook end-to-end on V2 staging with Tiw/admin
   allow-list populated.** This step is intentionally human-gated —
   Claude Dev cannot click the Meta App Dashboard webhook
   subscription. Follow the exact sequence in §1 → §11. Record the
   first-30-minute watch checklist results.

3. **If watch returns green**, open the next planned package: either
   (a) admin-only outbound response delivery behind a feature flag,
   or (b) gated UNIQUE promotion (`_pending_023`) with a fresh
   pre-apply duplicate audit.

4. **If anything in §9 watch fires**, follow runbook §10 immediately
   (V2_ADMIN_ONLY_TEST_MODE=false → remove webhook subscription →
   rotate tokens if leak suspected) and open an incident-tracking
   task.

5. **Production go-live still requires Tiw's explicit approval.** V2
   continues to operate behind the admin-only test posture; this QA
   does not unlock public webhook traffic or customer-wide replies.

## 8. Note on Source of Truth

`CURRENT_QA_TASK.md` specifies that QA must treat the GitHub repo
`tiwsoonkyu/tourfiremai-bot` on `v2/s4-followup-vision-ondemand` as
source of truth. The Cowork workspace does not have `.git` for this
project, so direct verification of commit `946790c` was not possible
from this session.

However:

- The Dev report explicitly notes Codex/Tiw will commit/push from a
  local clone, so the three artefacts in the workspace are the
  artefacts intended for that commit.
- The user directed QA against `QA-2026-05-20-016` against the files
  on disk. QA proceeded with the workspace-mirror files, matching
  the scope the user named — consistent with the precedent set by
  every QA cycle since `QA-2026-05-19-008`.

If Codex needs strict commit-level verification, Codex should re-run
the three pytest commands on a local clone after committing. The
numbers above (24 / 55 / 862) should reproduce exactly with flask
installed and a Linux-style tmpdir.

---

Reviewer: Claude QA
Verdict: `GO_WITH_NOTES`
Stops here for Codex.
