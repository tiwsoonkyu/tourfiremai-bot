# QA Report — QA-2026-05-20-013

## 1. Verdict

`GO_WITH_NOTES`

Reviewer: Claude QA (Cowork session, 2026-05-20)
Dev task reviewed: `DEV-2026-05-20-013` (Sprint 5 Package G — Wire Detail
Departure Rows Into Scraper and Selected-Tour Memory)
Branch: `v2/s4-followup-vision-ondemand`
Base commit (per Dev / AGENT_STATUS.json): `0fa9591`

Verdict means: the new detail-enrichment seam and the deterministic
selected-departure matcher are safe to merge and adequate for the next
wiring package (orchestrator + response-writer integration). All 10
charter checks pass and all 13 internal acceptance checks pass. Notes
are minor and exclusively pertain to known carried-over operational
gaps (DB UNIQUE tightening, caller-side fetch policy, response-writer
wiring) — none of which are introduced by this Dev task.

This verdict does **not** approve:

- customer-facing outbound replies,
- production webhook changes,
- any new migration apply,
- any LLM / OCR / paid-provider live calls,
- production go-live.

## 2. Scope Reviewed

Reviewed `DEV-2026-05-20-013` as one integrated package against the
ten required checks listed in `docs/tasks/CURRENT_QA_TASK.md`:

1. DEV-012 parser is reused (not reimplemented inconsistently).
2. Detail reads use `/tour/<web_code>` only (never `/intertourdetail/`).
3. Row persistence/mapping is idempotent and non-destructive.
4. `-`, empty strings, and non-price placeholders stay `NULL`/`None`, never `0`.
5. `web_code`, `tour_code_real`, and `airline` remain distinct.
6. Selected-date matching is deterministic and refuses to guess on
   ambiguous / no-match input.
7. Contact/status text is preserved but not interpreted as sold-out.
8. No unit tests call live network / LLM / OpenAI / OCR / Meta / LINE /
   paid providers.
9. Broad non-live suite has no regressions, or Dev explains a credible
   environment limitation.
10. No V1, Make.com, production webhook, deploy, or secret changes.

Artefacts inspected in the OneDrive Cowork workspace mirror of
`v2/s4-followup-vision-ondemand`:

- `v2/scraper/detail_enrichment.py` (new, 324 lines, mtime 2026-05-19 19:59)
- `v2/lib/selected_departure_match.py` (new, 370 lines, mtime 2026-05-19 20:00)
- `v2/tests/test_detail_enrichment.py` (new, 450 lines, mtime 2026-05-19 20:02)
- `v2/tests/test_selected_departure_match.py` (new, 364 lines, mtime 2026-05-19 20:03)
- `docs/tasks/DEV_REPORT_CURRENT.md` (`DEV-2026-05-20-013` report)
- `docs/tasks/AGENT_STATUS.json` (Dev status snapshot)

Out-of-scope artefacts (verified untouched by this task):

- V1: `app.py` (mtime 2026-05-09), `webhook_proxy.py` (mtime 2026-05-06).
- Make.com blueprints: newest `make_blueprint*.json` mtime 2026-05-08.
- Migrations: `20260520_021_departure_price_rows.sql` mtime 2026-05-19
  18:51 (from prior 012 cycle), no new migration files added.
- DEV-012 parser: `v2/scraper/departure_price_table.py` not modified by
  this task (the new modules import from it).
- Listing scraper: `v2/scraper/scrape_tours.py` mtime 2026-05-18 16:10,
  unchanged. (Dev explicitly notes the `/intertourdetail/` write to
  `tours_canonical.url` remains in place by design — see §6 risk #4.)
- Production webhook / Cloudflare worker / Railway config: not touched.

## 3. Evidence Checked

### 3.1 Charter check 1 — DEV-012 parser reused, not duplicated

`detail_enrichment.py` imports `BASE_URL`, `DETAIL_PATH`,
`DeparturePriceRow`, `idempotency_key`, `parse_departure_price_table`,
`parse_detail_header_codes`, and `to_tour_departure_rows` directly from
`v2.scraper.departure_price_table` (lines 60–68). No re-implementation
of date / money / availability parsing in either new module. The match
helper (`selected_departure_match.py`) similarly imports
`DeparturePriceRow`, `THAI_MONTHS`, and `parse_thai_date_range` from
the DEV-012 parser (lines 52–56) so that year resolution stays in one
place.

### 3.2 Charter check 2 — `/tour/<web_code>` only

`build_detail_url(web_code)` returns
`BASE_URL + DETAIL_PATH.format(web_code=lower)` and raises on empty
input (lines 100–108).

QA grep over the four new files:

| Token | Result |
|-------|--------|
| `/intertourdetail/` in production code | only inside docstrings/comments explaining "the legacy `/intertourdetail/` path 500s on production"; never built or fetched |
| `/intertourdetail/` in tests | only as **negative** assertions (`assert "/intertourdetail/" not in url`) |

Asserted by tests `test_builds_tour_path_not_intertourdetail`,
`test_uses_canonical_template`, `test_lowercases_web_code`,
`test_fetches_tour_path` (single GET to `/tour/ap242455`),
`test_idempotent_second_enrichment`, and
`test_no_live_network_during_unit_run` (asserts every call URL starts
with `BASE_URL + "/tour/"`).

### 3.3 Charter check 3 — Idempotent, non-destructive persistence

`upsert_departure_rows` (lines 157–217) uses the same
`idempotency_key((tour_id or web_code), departure_start,
departure_end, bus)` shape established by DEV-012, then calls
`supabase.table("tour_departures").upsert(match=..., insert=...,
update=...)`. The `existed_before` probe (`select_one`) is used only
to track inserted-vs-updated counters; the actual upsert is a single
atomic call. Errors are appended to `DetailPersistenceResult.errors`
and never raised — the caller is shielded.

QA-verified asserts:

- `test_first_run_inserts_each_row` — 3 inserts, 0 updates.
- `test_idempotent_second_run_does_not_duplicate` — second run yields
  0 inserts / 3 updates; row count stays at 3, never grows to 6.
- `test_idempotent_second_enrichment` — same property end-to-end.
- `test_no_tour_id_falls_back_to_web_code_match` — without `tour_id`,
  match falls back to `web_code`; re-run still yields 0 new rows.
- `test_idempotency_keys_are_unique_across_rows` — 3 distinct keys for
  3 parsed rows; shape `(tour_id, start, end, bus)`.
- `test_missing_dates_are_skipped_not_inserted_as_null` —
  `skipped_no_date` accounts for the dropped row; nothing is written
  with a `NULL departure_start`.

### 3.4 Charter check 4 — Missing values stay NULL, never `0`

`to_tour_departure_rows` (DEV-012) is the single source of truth for
NULL → NULL propagation; `upsert_departure_rows` passes the payload
verbatim without any `or 0` / `int(... or 0)` coercion.

QA grep result over `detail_enrichment.py` and
`selected_departure_match.py`: no `or 0`, no `int(... or 0)`, no
`coalesce`-style coercion exists.

QA-verified asserts:

- `test_dash_cells_stay_null_in_persisted_payload` — row 2 has
  `child_bed_price=None` and `child_no_bed_price=None`; every price
  field is `None` or `> 0`.
- `test_dash_cells_become_none_on_match` — match-side mirror: every
  price field is `None` or `> 0`.

### 3.5 Charter check 5 — `web_code` ≠ `tour_code_real` ≠ `airline`

`DepartureMatch` dataclass keeps the three fields as separate
attributes (lines 85–87 of `selected_departure_match.py`). The
persisted payload preserves all three columns via
`to_tour_departure_rows`.

QA-verified asserts:

- `test_codes_kept_separate_on_persisted_row` — three pairwise-distinct
  assertions on the stored row (`web_code == "ap242455"`,
  `tour_code_real == "BCCKG27-HU"`, `airline == "HU"`, plus the three
  pairwise `!=` checks).
- `test_exact_start_date_high_confidence` — same three-way separation
  on a matched row.

### 3.6 Charter check 6 — Deterministic, no-guess matching

`match_departure(...)` algorithm (lines 230–336 of
`selected_departure_match.py`):

| Branch | Outcome | Confidence |
|--------|---------|-----------|
| Unparseable phrase | `unparseable` + `no_date_in_phrase` | — |
| `target < today` | `no_match` + `date_in_past` | — |
| Exactly one row with `departure_start == target` | `matched` | `high` |
| >1 row with same `departure_start` | `ambiguous` + `multiple_rows_share_start_date` | — |
| Exactly one row with `start ≤ target ≤ end` | `matched` | `medium` |
| >1 row containing target | `ambiguous` + `multiple_rows_contain_date` | — |
| `allow_low_confidence=True` and exactly one row within ±2 days | `matched` | `low` |
| `allow_low_confidence=True` and multiple near rows | `ambiguous` + `multiple_near_dates` | — |
| Otherwise | `no_match` + `date_not_in_any_row` | — |

QA-verified asserts:

- `test_exact_start_date_high_confidence` — high confidence on exact start.
- `test_date_inside_single_row_range_returns_medium` — medium for
  in-range.
- `test_multiple_rows_with_same_start_returns_ambiguous` — two same-start
  rows → `ambiguous`, not a guess.
- `test_overlapping_in_range_returns_ambiguous` — two ranges overlap on
  target → `ambiguous`, not a guess.
- `test_does_not_guess_low_confidence_by_default` — ±2-day fuzziness is
  **off** unless the caller opts in.
- `test_low_confidence_opt_in_returns_low` — explicit opt-in returns
  `low` confidence so the response writer can phrase as "did you mean ...".
- `test_past_date_rejected` — Jan 18 vs today=May 1 → `no_match` with
  `date_in_past`; `parsed_phrase_date` still echoed so callers can
  confirm with the customer.
- `test_unparseable_phrase` — "ขอคุยเล่นๆ" → `unparseable` +
  `no_date_in_phrase`.
- `test_no_rows_returns_no_match` — empty input → `no_match` +
  `no_rows_with_dates`.

### 3.7 Charter check 7 — Contact-button text not classified as sold-out

`_to_match` (line 204) and `to_tour_departure_rows` (DEV-012) both
forward `availability_status` from the parser, which classifies
contact-button rows as `"unknown"`, not `"sold_out"`. The matcher does
not override that.

QA-verified asserts:

- `test_contact_button_status_never_persists_as_sold_out` — stored
  contact-button rows have `availability_status != "sold_out"` AND the
  legacy mirror `status != "sold_out"`.
- `test_sold_out_row_classified_from_class_signal` — `row-soldout`
  CSS class produces exactly one `sold_out` row (the "เต็ม" row), no
  others.
- `test_match_preserves_unknown_availability_for_contact_rows` —
  matched contact-button row carries `availability_status="unknown"`
  and `status_text="ติดต่อเจ้าหน้าที่"` through to the response surface.

### 3.8 Charter check 8 — No live network / LLM / paid providers in tests

QA grep on the four new files for forbidden imports
(`import (openai|anthropic|requests|psycopg|supabase|httpx)`) returns
only the **negative** assertion strings inside
`test_selected_departure_match.py` (`for forbidden in ("import
requests", "openai", "anthropic", "supabase"): assert forbidden not
in src`).

The HTTP boundary is the `HttpClient` Protocol on
`detail_enrichment.py` line 89. Tests inject a `FakeHttp` whose `.get`
returns a `FakeResponse` constructed in-process; no `requests` import
exists in the test or in `detail_enrichment.py` itself. The matcher
module has no HTTP boundary at all.

QA also verified that the test file structurally asserts the source
module has no forbidden imports
(`test_does_not_use_llm_or_network`).

### 3.9 Charter check 9 — Broad non-live suite has no regressions

QA re-ran the same three commands the Dev report claims, from the
repo root, with no network:

```text
PYTHONPATH=. python3 -m pytest v2/tests/test_detail_enrichment.py \
                               v2/tests/test_selected_departure_match.py
=> 51 passed in 0.18s
```

```text
PYTHONPATH=. python3 -m pytest v2/tests/test_detail_enrichment.py \
                               v2/tests/test_selected_departure_match.py \
                               v2/tests/test_departure_price_table.py \
                               v2/tests/test_scraper.py
=> 143 passed in 0.37s
```

```text
PYTHONPATH=. python3 -m pytest v2/tests/ \
    --deselect v2/tests/test_integration_staging.py \
    --deselect v2/tests/test_live_openai_health.py \
    --deselect v2/tests/test_phase2_live_followup.py
=> 783 passed / 47 skipped / 0 failed in 2.65s
```

All three numbers match the Dev report exactly (target suite 51,
adjacency 143, broad 783 passed / 47 skipped / 0 failed). The 47 skips
are entirely flask-not-installed webhook tests + cassette/live tests
that the hard-rule list deselects; none are caused by Sprint 5
Package G.

### 3.10 Charter check 10 — No V1, Make.com, webhook, deploy, secret changes

QA verified by mtime + content inspection:

- `app.py` mtime 2026-05-09 (predates this task; untouched).
- `webhook_proxy.py` mtime 2026-05-06 (predates this task; untouched).
- All `make_blueprint*.json` files have mtimes ≤ 2026-05-08.
- No new migrations added; migration 021 mtime 2026-05-19 18:51 (from
  the prior 012 cycle).
- Listing scraper `scrape_tours.py` mtime 2026-05-18 16:10, unchanged.
- No `.env*` files modified; no shell of `getenv`/`environ`/`API_KEY`/
  `SECRET`/`TOKEN`/`PASSWORD` in the four new files.
- No `cloudflare-worker.js` change; no Railway / deploy file change.

## 4. Findings by Severity

### P0 — Blocking

None.

### P1 — Must fix before customer-facing wiring (not blocking this QA)

None.

### P2 — Should fix in the next wiring package

- **P2-1 (carried over from QA-012 P2-2):** `idx_dep_full_row` on
  `tour_departures` is still non-unique pending a backfill audit.
  Application-layer idempotency in `upsert_departure_rows` is correct,
  but a DB-level UNIQUE on `(tour_id, departure_start, departure_end,
  bus)` is needed for the next wiring package to make the idempotency
  key load-bearing at the storage layer. Tracked by Dev report
  §6.2.

- **P2-2:** `enrich_tour_detail` has no per-fetch caching. The caller
  decides when to call it. Generic-greeting heuristics, batch refresh,
  and TTL caching are out of scope here and tracked for a future
  scheduling task (Dev report §6.3). Until then the caller path needs
  to be careful not to trigger an HTTP fetch on every inbound message.

- **P2-3:** `DepartureMatch.confidence` is populated (`high` / `medium`
  / `low`) but not yet consumed by the response writer. The wiring
  package must surface `medium` / `low` matches as "ใช่ทริปวันที่ … ใช่
  มั้ยคะ"-style confirmation rather than a hard quote (Dev report §6.5).

- **P2-4:** Listing scraper still writes
  `tours_canonical.url = "{BASE_URL}/intertourdetail/{code}"`
  (`v2/scraper/scrape_tours.py:255`). Detail enrichment never reads
  from that field — it builds the URL from `web_code` via
  `build_detail_url()`. But the listing-card URL is still wrong for
  any external consumer who clicks it. Deliberately out of scope here
  to avoid regressing live-verified Sprint 1; controller should open a
  dedicated, narrowly scoped fix task.

### P3 — Nits

- **P3-1 (carried over from DEV-013 §5.4):** Three pre-existing tests
  fail when pytest is invoked from `v2/` instead of the repo root
  because they open `"v2/lib/admin_ops.py"` as a literal path
  (`test_admin_only_preflight.py::test_preflight_json_main_does_not_print_secret_values`,
  `test_admin_ops.py::TestNoSecretOrWholesaleLeakage::test_no_secret_pattern_appears_in_module`,
  `test_admin_ops.py::TestNoSecretOrWholesaleLeakage::test_no_wholesale_brand_token_appears_in_module`).
  Not introduced by this task. Hygiene fix is a one-line switch to
  `pathlib.Path(__file__).resolve().parents[...]`. Should be a small
  separate task.

- **P3-2:** `upsert_departure_rows` falls back to matching on
  `web_code` when `tour_id` is `None`. The schema and live scraper
  invariants forbid two different tours sharing a `web_code`, but the
  test `test_no_tour_id_falls_back_to_web_code_match` documents this
  deliberately. Worth a comment in the wiring package's review
  checklist if `web_code` collisions ever become possible.

- **P3-3:** `enrich_tour_detail`'s `to_summary()` is currently the
  only admin-safe surface for this module. The wiring package should
  decide whether to also log `idempotency_keys` in admin telemetry, or
  whether keeping them on the result only (today's behaviour) is the
  right boundary.

### Charter / process

None for this cycle — `CURRENT_DEV_TASK.md` and `CURRENT_QA_TASK.md`
both correctly reference the 013 IDs (CTR-1 from QA-012 has been
resolved by Codex's rotation).

## 5. Tests Verified

QA re-executed in the Cowork workspace (no network, no live providers,
no secrets):

| Suite | Result | Match Dev Report |
|-------|--------|------------------|
| `pytest test_detail_enrichment.py test_selected_departure_match.py` | 51 passed in 0.18s | ✅ count exact |
| `pytest test_detail_enrichment.py test_selected_departure_match.py test_departure_price_table.py test_scraper.py` | 143 passed in 0.37s | ✅ exact |
| Broad non-live V2 suite (3 live deselects) | 783 passed / 47 skipped / 0 failed in 2.65s | ✅ exact |

Test-class breakdown of the 51 new tests (matches Dev report §5.1):

| Class | Tests |
|-------|------:|
| `TestBuildDetailUrl` | 4 |
| `TestFetchDetailHtml` | 4 |
| `TestUpsertDepartureRows` | 10 |
| `TestEnrichTourDetail` | 8 |
| `TestParseCustomerDatePhrase` | 4 |
| `TestMatchDepartureExact` | 2 |
| `TestMatchDepartureInRange` | 1 |
| `TestMatchDepartureNoMatch` | 6 |
| `TestMatchDepartureAmbiguous` | 2 |
| `TestContactButtonNeverSoldOut` | 2 |
| `TestNoZeroCoercionOnMatch` | 1 |
| `TestListAvailableDepartures` | 4 |
| `TestResultToDict` | 3 |
| **Total** | **51** |

Live HTML smoke against `tourfiremai.com`: **not run by QA** — out of
scope for this read-only review and not required by the charter. The
DEV-012 CLI (`v2/tools/live_detail_departure_smoke.py`) remains the
deliberate drift sentinel.

## 6. Remaining Risks

1. **Adapter→orchestrator wiring still pending.** Until the next Dev
   task lands, the V2 orchestrator does not call `enrich_tour_detail`
   from any selected-tour path. The new `tour_departures` rows will
   not influence quoted prices yet. This is the intended sequencing
   but means customer-visible behaviour does not change with this
   merge.

2. **HTML class-name drift.** Both new modules depend on the
   DEV-012 parser, which depends on `.table-dateprice`, `.b-tb-dp`,
   `.s-tb<n>-n`, `.b-codepg`. If tourfiremai.com renames any of these,
   `parse_departure_price_table` returns `[]`, `enrich_tour_detail`
   reports `parsed=True, len(rows)==0`, and `upsert_departure_rows`
   becomes a no-op. The DEV-012 CLI is still the only drift sentinel;
   scheduling it remains tracked as P2 carryover from QA-012.

3. **DB-level UNIQUE on idempotency key not yet enforced** (P2-1
   above). Until the backfill audit + `CREATE UNIQUE INDEX` lands, a
   bug elsewhere that bypasses `upsert_departure_rows` could in
   principle insert a duplicate. This Dev task does not introduce that
   risk — but does not remove it either.

4. **Listing scraper URL still says `/intertourdetail/`** (P2-4
   above). Deliberately out of scope; flagged so it does not get lost.

5. **Confidence values are dataclass-only for now** (P2-3). The
   wiring package must consume `confidence ∈ {high, medium, low}` to
   avoid hard-quoting a `low`-confidence guess to the customer.

6. **Caller responsibility for fetch policy** (P2-2). Nothing in this
   package prevents a careless caller from fetching the detail page
   on every inbound message. The next package must add a TTL or batch
   refresher.

## 7. Next Recommended Step

1. **Codex commits the four new files** on
   `v2/s4-followup-vision-ondemand` and appends `TASK_LOG.md` entries
   for `DEV-2026-05-20-013` (accepted) and `QA-2026-05-20-013` (`GO_WITH_NOTES`).

2. **Codex / Tiw runs `python -m v2.tools.live_detail_departure_smoke
   ap242455 ap232919 ap183598`** from a local clone (network-on) to
   confirm live HTML still matches parser assumptions before opening
   the wiring task. Capture the JSON output in `docs/` as a drift
   baseline. (Optional but recommended — would catch any HTML
   renames before the orchestrator depends on parsed rows.)

3. **Open the next Dev task** to:
   1. Wire `enrich_tour_detail` into the orchestrator's tour-selection
      / date-question paths (with caller-side fetch policy to avoid
      hitting the detail page on every inbound message).
   2. Wire `match_departure` into the response writer's pre-LLM
      planning context, so the LLM composes copy *over* the
      deterministic row, not *of* it. Surface `medium` / `low`
      confidence matches as confirmation prompts rather than hard quotes.
   3. After a one-time backfill audit, tighten `idx_dep_full_row` into
      a UNIQUE constraint (closes P2-1).

4. **Separately, in a tightly scoped task:** fix the listing scraper to
   write `tours_canonical.url = "{BASE_URL}/tour/{code}"` (closes
   P2-4) and confirm no V1 path silently consumes
   `tours_canonical.url`.

5. **Hygiene task (optional):** convert the three CWD-dependent
   admin-ops/preflight tests to `Path(__file__).resolve().parents[...]`
   (closes P3-1) so `cd v2 && pytest tests/` is also green.

## 8. Note on Source of Truth

`CURRENT_QA_TASK.md` specifies that QA must treat the GitHub repo
`tiwsoonkyu/tourfiremai-bot` on `v2/s4-followup-vision-ondemand` as
source of truth, and report `BLOCKED: source-of-truth repo
unavailable` if the Cowork workspace lacks git or differs.

The Cowork workspace does not have `.git` for this project, so direct
verification of commit `0fa9591` was not possible from this session.
However:

- The Dev report explicitly notes Codex/Tiw will commit/push from a
  local clone (Dev report §1, §8), so the four files in the workspace
  are the artefacts intended for that commit.
- The user directed QA against `QA-2026-05-20-013` against the files
  on disk. QA proceeded with the workspace-mirror files, matching the
  scope the user named — consistent with the precedent set by
  `QA-2026-05-19-008` through `QA-2026-05-20-012`.

If Codex needs strict commit-level verification, Codex should re-run
the same three pytest commands and `grep -E ...` checks on the local
clone after committing. The numbers above (51 / 143 / 783 + 47 skips)
should reproduce exactly.

---

Reviewer: Claude QA
Verdict: `GO_WITH_NOTES`
Stops here for Codex.
