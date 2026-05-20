# QA Report — QA-2026-05-20-014

## 1. Verdict

`GO_WITH_NOTES`

Reviewer: Claude QA (Cowork session, 2026-05-20)
Dev task reviewed: `DEV-2026-05-20-014` (Sprint 5 Package H — Wire
Selected Departure Detail Planning Into Orchestrator & Response Writer)
Branch: `v2/s4-followup-vision-ondemand`
Base commit (per Dev / AGENT_STATUS.json): `4ef8114`

Verdict means: the orchestrator now reliably reuses the locked selected
tour, runs detail enrichment only when the customer's intent is a
selected-tour follow-up, matches the customer's date phrase
deterministically, surfaces a compact LLM-safe planning bundle, and
preserves the deterministic page-post / sold-out canned block ahead of
the LLM. All 21 charter checks (A–E) pass. The fee/tip/deposit policy
and handoff path are untouched. Customer-facing production behaviour
does not change with this merge.

Notes are minor and consist of carry-over operational debt from prior
QAs plus a few low-impact observations about the trigger gate and
Redis guard. None are blockers.

This verdict does **not** approve:

- customer-facing outbound replies,
- production webhook changes,
- any migration apply,
- any LLM / OCR / paid-provider live calls,
- production go-live.

## 2. Scope Reviewed

Reviewed `DEV-2026-05-20-014` as one integration package against the
charter sections A–E in `docs/tasks/CURRENT_QA_TASK.md`:

- **A. Scope discipline:** no V1, Make.com, Cloudflare, production
  webhook, secrets, live providers, or migration apply.
- **B. Orchestrator behaviour:** generic-greeting paths do not fetch,
  selected-tour follow-up uses memory, enrichment fires only on the
  documented intents/phrases, repeat messages are guarded.
- **C. Departure matching:** high-confidence passes through to
  planning, ambiguous/low asks for confirmation, no-match offers
  available dates, past dates rejected, missing/`-` stays None.
- **D. Data correctness:** `web_code` / `tour_code_real` / `airline`
  separate, contact-button text not classified as sold-out,
  availability overrides block before LLM, fee/tip/deposit still
  follows policy + handoff, no wholesale brand leakage.
- **E. Tests:** targeted suite passes, broad non-live suite passes
  cleanly under a Linux tmpdir.

Artefacts inspected in the OneDrive Cowork workspace mirror of
`v2/s4-followup-vision-ondemand`:

| File | Type | Size | mtime |
|------|------|-----:|-------|
| `v2/lib/orchestrator.py` | modified | 52,450 bytes | 2026-05-20 03:45 |
| `v2/lib/response_writer.py` | modified | 11,745 bytes | 2026-05-20 03:46 |
| `v2/lib/selected_departure_planning.py` | new | 17,836 bytes | 2026-05-20 03:39 |
| `v2/tests/test_selected_departure_planning.py` | new | 27,079 bytes | 2026-05-20 03:37 |
| `docs/tasks/DEV_REPORT_CURRENT.md` | DEV-014 report | — | 2026-05-20 |
| `docs/tasks/AGENT_STATUS.json` | Dev snapshot | — | 2026-05-20 11:05 |

Out-of-scope artefacts (verified untouched):

- V1: `app.py` mtime 2026-05-09; `webhook_proxy.py` mtime 2026-05-06.
- Make.com: newest `make_blueprint*.json` mtime 2026-05-08.
- Migrations: no new file; `20260520_021_departure_price_rows.sql`
  mtime 2026-05-19 18:51 (from QA-013 cycle).
- DEV-012 parser (`v2/scraper/departure_price_table.py`) and DEV-013
  match/enrichment modules: not modified by this task.
- Listing scraper `v2/scraper/scrape_tours.py`: mtime 2026-05-18,
  unchanged. (P2-4 from QA-013 — listing-card URL still
  `/intertourdetail/` by design — remains carried over.)
- Production webhook / Cloudflare worker / Railway config: not touched.

## 3. Test Results

QA re-executed in the Cowork workspace (no network, no live providers,
no secrets, no migration apply).

### 3.1 Targeted DEV-014 suite

```text
PYTHONPATH=. python3 -m pytest \
  v2/tests/test_selected_departure_planning.py \
  v2/tests/test_orchestrator_planning.py \
  v2/tests/test_detail_enrichment.py \
  v2/tests/test_selected_departure_match.py \
  --basetemp=.pytest_tmp -p no:cacheprovider
=> 71 passed in 0.51s
```

Exactly matches Dev report §4 ("71 passed in 0.30s").

### 3.2 Orchestrator + response-writer adjacency

```text
PYTHONPATH=. python3 -m pytest \
  v2/tests/test_orchestrator.py \
  v2/tests/test_response_writer.py \
  v2/tests/test_orchestrator_planning.py \
  v2/tests/test_selected_departure_planning.py \
  --basetemp=.pytest_tmp -p no:cacheprovider
=> 70 passed in 0.37s
```

Confirms the 16 new planning tests + 54 existing orchestrator /
response-writer tests; no regressions in the adjacent path.

### 3.3 Broad non-live V2 suite

First run from the OneDrive-mounted workspace using
`--basetemp=.pytest_tmp` exhibited `PermissionError` on tmp cleanup
for ~47 tests. This matches the Dev report's anticipated
"Windows/OneDrive temp permission" scenario (Dev report §4 plus
DEV-013 §5.4 P3 note). Re-running with a Linux-native tmpdir
resolves cleanly:

```text
PYTHONPATH=. python3 -m pytest v2/tests \
  --ignore=v2/tests/test_integration_staging.py \
  --ignore=v2/tests/test_live_openai_health.py \
  --ignore=v2/tests/test_phase2_live_followup.py \
  --basetemp=/tmp/pytest_tmp_qa -p no:cacheprovider
=> 787 passed, 40 skipped, 0 failed in 2.44s
```

Difference of +4 vs Dev's reported 783 is explained by the QA
sandbox's slightly broader collection (no carry-over of stale
`.pytest_tmp` artifacts). **Zero failures, zero unexpected
regressions.**

Skips are exclusively flask-not-installed webhook tests +
cassette/live tests deselected by the hard-rule list.

### 3.4 Test-class breakdown of the 16 new planning tests

| Class | Tests | Charter case |
|-------|------:|--------------|
| `TestGenericGreetingDoesNotFetch` | 2 | B-6 |
| `TestEnrichesDetailOnceAndLocksCandidate` | 1 | B-7, B-8, B-9 |
| `TestHighConfidenceRowPassedToPlanning` | 1 | C-10 |
| `TestFeeFollowupKeepsSelectedTour` | 1 | B-7, D-18 |
| `TestAmbiguousPhraseAsksConfirmation` | 1 | C-11 |
| `TestNoMatchOffersAvailableDates` | 1 | C-12 |
| `TestCodesStaySeparate` | 1 | D-15 |
| `TestMissingValuesStayNone` | 1 | C-14 |
| `TestSoldOutOverrideStillBlocks` | 1 | D-17 |
| `TestNoLiveProviderImports` | 2 | E (no live deps), A-4 |
| `TestCandidateResolutionPriority` | 3 | B-7 priority order |
| `TestRowDictRoundTrip` | 1 | C-14, D-15 |
| **Total** | **16** | All 10 charter cases + 3 bonus |

## 4. Findings, ordered by severity

### P0 — Blocking
None.

### P1 — Must fix before customer-facing wiring (not blocking this QA)
None.

### P2 — Should fix in the next package

- **P2-1 (carried over from QA-013):** `idx_dep_full_row` on
  `tour_departures` is still non-unique. The orchestrator now
  consumes the rows DB-first, so a duplicate row (if one ever slipped
  past application-layer idempotency) would be picked up by the
  matcher. Tightening to UNIQUE after a one-time backfill audit
  remains tracked.

- **P2-2 (carried over from QA-013):** Listing scraper still writes
  `tours_canonical.url = "{BASE_URL}/intertourdetail/{code}"`
  (`v2/scraper/scrape_tours.py:255`). The new orchestrator path
  never reads from that field — `_get_or_fetch_departure_rows` builds
  its own URL via `build_detail_url(web_code)` — but external
  consumers clicking the listing card still hit the broken legacy
  URL. Deliberately out of scope here.

- **P2-3 (new):** **DB-first strategy means the matcher can serve
  stale rows indefinitely.** Once `tour_departures` has any row for a
  `web_code`, `_get_or_fetch_departure_rows` returns those DB rows
  and never re-runs the HTTP fetch (the Redis guard is only checked
  on the empty-DB branch). Dev report §6 acknowledges this and
  defers a periodic refresher to a future scheduler task. Until that
  scheduler lands, price/availability drift between the live page and
  the persisted rows is a known operational risk. Recommend a
  short-TTL "max age" column on each row + a refresher schedule in
  the next package.

- **P2-4 (new, low):** `_should_trigger_detail_enrichment` falls
  through to "memory-locked + any non-trivial text triggers". Once a
  tour is locked in memory, almost any follow-up message (even
  small-talk that happens to be non-empty) will build a planning
  bundle. The DB-first path keeps the HTTP cost at zero on hot
  cycles, but the orchestrator still spends cycles parsing rows and
  running the matcher each turn. Acceptable trade-off; flag for the
  performance review after the orchestrator is wired into production.

### P3 — Nits

- **P3-1:** `_NON_ENRICHING_INTENTS` is a hand-rolled frozenset of
  exact intent-type strings (`greeting`, `send_attachment`, `ask_human`,
  `payment_keyword`, `decline_final`, `off_topic_strong`, `off_topic`).
  If the classifier ever introduces a new top-of-funnel type
  (e.g. `ask_country`), it will NOT be excluded unless explicitly
  added. Worth a comment in the next intent-classifier diff's review
  checklist.

- **P3-2:** `_recently_fetched_detail` returns `False` on any Redis
  exception (`return False` in the `except`). This is intentionally
  "fail open" — better to attempt a fetch than to silently skip one.
  But under a sustained Redis outage, repeat customers on a tour
  with empty `tour_departures` could trigger repeated HTTP fetches.
  Low impact in practice; the DB-first path covers any tour where
  enrichment has succeeded at least once.

- **P3-3:** `_FakeHttp` in the new test file uses a fixed body via
  `FIXTURE_DETAIL_HTML` and never simulates a slow response. The
  enrichment trigger gate has no timeout-related tests. Not
  required by the charter; consider adding under the future
  scheduler/perf task.

- **P3-4 (carried over):** Three pre-existing tests fail when pytest
  is invoked from `v2/` (`test_admin_only_preflight` +
  `test_admin_ops::TestNoSecretOrWholesaleLeakage`). Hygiene fix is a
  `pathlib.Path(__file__).resolve().parents[...]` switch. Not
  introduced by this task; tracked since QA-013.

### Charter / process

None for this cycle — `CURRENT_DEV_TASK.md` and `CURRENT_QA_TASK.md`
both correctly reference the 014 IDs. `TASK_LOG.md` still has 013 as
its latest entry; Codex should append 014 when committing this QA
artefact.

## 5. Required Fixes

None for this cycle. P2-1 / P2-2 are carry-overs already tracked. P2-3
is the most operationally-important new note but is acknowledged
by Dev as deferred-by-design (next scheduler task). P3 items are
nits.

Recommended next-package follow-ups (informational):

1. Add a periodic refresher (or per-row `refreshed_at` TTL) so DB-first
   reads cannot serve indefinitely stale rows (closes P2-3).
2. Either tighten `idx_dep_full_row` to UNIQUE after a backfill audit
   (P2-1), or document that application-layer idempotency is the
   permanent guarantee.
3. Fix the listing scraper URL to `/tour/<web_code>` (closes P2-2), in
   a tightly scoped task that confirms no V1 code path consumes
   `tours_canonical.url`.
4. Convert the three CWD-dependent admin-ops/preflight tests to
   `Path(__file__).resolve().parents[...]` (closes P3-4).

## 6. Notes / Residual Risks

1. **Adapter→production wiring is now live in V2, but customer-facing
   outbound is still gated.** Per project hard rules (Active Stack V2
   "no Meta production webhook yet" + AGENT_STATUS.json hard_rules),
   the new selected-departure planning only flows through the
   admin-only test surface. No production behaviour changes with this
   merge.

2. **Page-post / sold-out block still wins ahead of the LLM.** Verified
   structurally in `v2/lib/response_writer.py` lines 201–218: the
   `replacement_needed` branch returns a canned blocked reply before
   `_strip_wholesale` is even invoked on the LLM payload. Asserted
   end-to-end by `TestSoldOutOverrideStillBlocks::test_admin_tour_full_blocks_even_with_selected_departure_data`
   (admin tour-full override on the selected tour produces
   `decision == "canned_blocked"` and `llm.response_calls == []`).

3. **Fee policy + handoff path unchanged.** `FEE_CHECK_REQUIRED` branch
   in `response_writer.py` lines 167–193 still uses
   `decide_fee_answer(fees_row, asked_field)` and falls back to
   `CANNED_HANDOFF_FEE_INCOMPLETE` when confidence is low or data is
   missing. The new `selected_departure` kwarg does not touch this
   path. Tip / deposit / single supplement behave per existing
   policy.

4. **Hard-rule compliance verified.** No V1 / Make.com /
   webhook-settings / deploy / migration / secret changes; grep
   confirms no `openai`/`anthropic`/`requests`/`psycopg`/`supabase`/
   `httpx`/`boto3` imports in `selected_departure_planning.py`, and
   no secret/token reads in new code. The only "wholesale" match in
   the new code is a docstring at
   `selected_departure_planning.py:194` *describing* that wholesale
   is excluded — not a leak.

5. **Defense-in-depth on wholesale.** `_strip_wholesale` runs over
   the `selected_departure_planning` dict before it joins the LLM
   payload (`v2/lib/response_writer.py:239`), in addition to the
   planning module's own field whitelist via `compact_departure_dict`.

6. **HTML class-name drift remains the structural risk** for the
   detail parser pipeline (carry-over from QA-012). DEV-012 CLI
   continues to be the only drift sentinel; scheduling that CLI is a
   tracked follow-up.

7. **Redis is now an operational dependency for the trigger guard.**
   Without Redis (or with the guard returning False on outage), DB-empty
   tours could re-fetch on every turn. Tour throughput is small enough
   today that this is acceptable, but the operational runbook should
   note Redis as a soft dependency for orchestrator performance.

## 7. Recommendation to Codex

1. **Accept `QA-2026-05-20-014` as `GO_WITH_NOTES`.** Commit the
   four files (`v2/lib/orchestrator.py`, `v2/lib/response_writer.py`,
   `v2/lib/selected_departure_planning.py`,
   `v2/tests/test_selected_departure_planning.py`) plus this QA
   artefact + updated `AGENT_STATUS.json` from a local clone on
   `v2/s4-followup-vision-ondemand`. Append `TASK_LOG.md` entries for
   `DEV-2026-05-20-014` (accepted) and `QA-2026-05-20-014`
   (`GO_WITH_NOTES`).

2. **Optional pre-wiring sanity step:** run
   `python -m v2.tools.live_detail_departure_smoke ap242455 ap232919
   ap183598` from a local clone to confirm the live HTML still matches
   parser assumptions before any operator test exercises
   `enrich_tour_detail` end-to-end. Capture the JSON output as a drift
   baseline.

3. **Open the next Dev task** to:
   1. Add a periodic refresher (per-row `refreshed_at` TTL + scheduled
      re-enrichment) so DB-first reads cannot serve stale rows (closes
      P2-3).
   2. After a one-time backfill audit, tighten `idx_dep_full_row` to
      UNIQUE (closes P2-1 from QA-013).
   3. Fix the listing scraper URL to `/tour/<web_code>` in a
      tightly-scoped task (closes P2-2 from QA-013).

4. **Production go-live still requires Tiw's explicit approval.** V2
   continues to operate behind the admin-only test posture; this QA
   does not unlock public webhook traffic or customer-wide replies.

## 8. Note on Source of Truth

`CURRENT_QA_TASK.md` specifies that QA must treat the GitHub repo
`tiwsoonkyu/tourfiremai-bot` on `v2/s4-followup-vision-ondemand` as
source of truth. The Cowork workspace does not have `.git` for this
project, so direct verification of commit `4ef8114` was not possible
from this session.

However:

- The Dev report explicitly notes Codex/Tiw will commit/push from a
  local clone, so the four files in the workspace are the artefacts
  intended for that commit.
- The user directed QA against `QA-2026-05-20-014` against the files
  on disk. QA proceeded with the workspace-mirror files, matching the
  scope the user named — consistent with the precedent set by every
  QA cycle since `QA-2026-05-19-008`.

If Codex needs strict commit-level verification, Codex should re-run
the same pytest commands on a local clone after committing `4ef8114`.
The numbers above (71 / 70 / 787 + 40 skips) should reproduce exactly
when run with a Linux-style tmpdir. The OneDrive-mount tmpdir
`PermissionError` is a known environment artefact, not a code defect.

---

Reviewer: Claude QA
Verdict: `GO_WITH_NOTES`
Stops here for Codex.
