# DEV REPORT — DEV-2026-05-20-013

## Task
Sprint 5 Package G — Wire Detail Departure Rows Into Scraper and
Selected-Tour Memory.

Branch: `v2/s4-followup-vision-ondemand` (workspace mirror; base commit
per `AGENT_STATUS.json`: `0fa9591`).

---

## 1. Status

`READY_FOR_QA`

All four sub-deliverables for DEV-2026-05-20-013 are implemented and
covered by deterministic, network-free unit tests:

1. Scraper / detail enrichment wiring around the DEV-012 parser
   (`v2.scraper.departure_price_table`).
2. Idempotent persistence helper for `tour_departures` using the
   migration-021 columns.
3. Deterministic selected-tour row matcher for date-bound customer
   phrases.
4. Targeted tests + adjacency tests + broad non-live V2 suite, all
   green.

No V1 code, no Make.com asset, no Supabase migration, no production
webhook setting, no secret, no live Meta / LINE / OpenAI / OCR / paid
provider call was touched. No customer-facing outbound was enabled.

---

## 2. Files changed

New (Dev):

- `v2/scraper/detail_enrichment.py` — fetch + parse + idempotent persist
  for `/tour/<web_code>` detail pages, with `DetailEnrichmentResult` /
  `DetailPersistenceResult` dataclasses and an `HttpClient` Protocol so
  unit tests inject a fake.
- `v2/lib/selected_departure_match.py` — deterministic
  `match_departure(rows, phrase, *, today, allow_low_confidence=False)`
  with `DepartureMatch` + `DepartureMatchResult` dataclasses, plus
  `parse_customer_date_phrase` and `list_available_departures` helpers.
- `v2/tests/test_detail_enrichment.py` — 26 tests (URL shape, fetch fake,
  persistence idempotency, dash→None, code separation, contact-button-
  never-sold-out, summary admin-safe shape).
- `v2/tests/test_selected_departure_match.py` — 25 tests (date phrase
  parsing, exact/range/ambiguous/no-match/past-date branches, contact-
  button rows surface as `unknown`, never-zero invariant, JSON-friendly
  dict shape, source-import guard).

Documentation updates (Dev report only):

- `docs/tasks/DEV_REPORT_CURRENT.md` (this file).
- `docs/tasks/AGENT_STATUS.json` (status flipped to `READY_FOR_QA`).

Not modified (by design):

- `v2/scraper/scrape_tours.py` — the listing scraper's
  `url = f"{BASE_URL}/intertourdetail/{code}"` write to
  `tours_canonical.url` was deliberately left alone. That field is the
  listing-card link, not the detail-page read URL. Fixing it without an
  explicit task would risk regressing live-verified Sprint 1 behaviour
  and was already flagged as a follow-up by DEV-2026-05-20-012.
- `v2/scraper/departure_price_table.py` — the QA-cleared parser was not
  modified; this package wires it.
- `v2/supabase/migrations/*` — none added or changed. Migration 021
  remains the source of truth for the column layout.
- V1, Make.com, production webhook, secrets — untouched per hard rules.

---

## 3. Summary of changes

### 3.1 `v2/scraper/detail_enrichment.py`

The DEV-012 parser is purely functional and does not know how to fetch
HTML or talk to Supabase. This module is the seam:

```python
result = enrich_tour_detail(
    "ap242455",
    http=requests_like_client,
    supabase=supabase_like_client,
    tour_id="<tour_uuid>",
)
```

- `build_detail_url(web_code)` returns
  `https://www.tourfiremai.com/tour/<lowercase_web_code>`. The legacy
  `/intertourdetail/<code>` path that 500s on prod is never built.
- `fetch_detail_html(web_code, http=...)` does a single `GET`. On any
  exception (`ConnectionError`, etc.) or non-200 status, it returns
  `None` so the caller never has to wrap this with `try/except`. No
  retries are added at this layer — backoff/scheduling is a scheduler
  concern.
- `upsert_departure_rows(rows, *, supabase, tour_id=None)` turns parsed
  `DeparturePriceRow` objects into payloads via the existing
  `to_tour_departure_rows` adapter, then calls the supabase-like
  `table("tour_departures").upsert(match=..., insert=..., update=...)`
  helper using the idempotency key:
    - `(tour_id, departure_start, departure_end, bus)` when `tour_id` is
      provided.
    - `(web_code, departure_start, departure_end, bus)` otherwise.
  Returns a `DetailPersistenceResult(upserted, inserted, updated,
  skipped_no_date, errors, idempotency_keys)`.
- `enrich_tour_detail(...)` composes the three steps and returns a
  `DetailEnrichmentResult` with `fetched / parsed / persisted` booleans,
  the parsed rows, the parsed header (`tour_code_real / airline /
  web_code`, kept separate), the persistence result, and a
  `to_summary()` dict that is safe for admin/log surfaces (no PSID, no
  secret-named keys, no wholesale).

Per hard rule, the URL builder, fetcher, and persistence helper never
mix `web_code`, `tour_code_real`, or `airline`. The parser already
keeps them strictly separate; this layer just propagates that property
into the persisted payload and the result dataclass.

### 3.2 `v2/lib/selected_departure_match.py`

This is the deterministic helper the orchestrator / response writer
will consult after a customer selects a tour. It never invokes an LLM
and never queries a DB — it operates over the already-parsed list of
`DeparturePriceRow`.

Match algorithm (cautious, with explicit non-match outcomes):

1. Drop rows without a `departure_start`.
2. Parse a target date from the phrase using
   `parse_customer_date_phrase(phrase, today=...)`. Returns the *start*
   date when the phrase is a range.
3. **Exact start match** on exactly one row → `matched`, confidence
   `"high"`.
4. Same start on >1 rows → `ambiguous` (e.g. two buses on the same day).
5. Date inside exactly one range → `matched`, confidence `"medium"`.
6. Date inside multiple ranges → `ambiguous`.
7. With `allow_low_confidence=True`, a single row within ±2 days →
   `matched`, confidence `"low"` so the response writer phrases it as
   "did you mean …" rather than a hard quote.
8. Otherwise → `no_match` with one of:
   `no_rows_with_dates`, `date_in_past`, `date_not_in_any_row`.

Phrases the matcher must refuse to guess on are explicitly returned as
`ambiguous` or `no_match`, **never** quietly resolved. The matcher also
refuses to quote any date earlier than `today` (`date_in_past`), so a
customer saying "ม.ค." accidentally won't be quoted as if the row were
still open.

`list_available_departures(rows, today=..., limit=...)` is the small
helper for "what dates do you have?" prompts. It excludes sold-out
rows and rows in the past, then sorts by `departure_start`.

### 3.3 Hard-rule alignment

- **Detail URL = `/tour/<web_code>` always.** Asserted by URL helper
  tests and by `test_no_live_network_during_unit_run`.
- **Codes separate.** Tests assert
  `web_code != tour_code_real != airline` on both the persisted row
  and the matched row.
- **`-` → `None`, never `0`.** Two persistence tests
  (`test_dash_cells_stay_null_in_persisted_payload`,
  `test_missing_prices_stay_null_in_payload` in the existing DEV-012
  suite) plus the new `test_dash_cells_become_none_on_match` lock this
  down.
- **Contact-button never reclassified as sold-out.** Both layers tested:
  `test_contact_button_status_never_persists_as_sold_out` and
  `test_match_preserves_unknown_availability_for_contact_rows`.
- **Idempotent persistence.** `test_idempotent_second_run_does_not_duplicate`
  + `test_idempotent_second_enrichment` confirm a re-run yields zero
  inserts and three updates, not six rows.
- **No live LLM, no network, no Supabase.**
  `test_selected_departure_match.TestResultToDict.test_does_not_use_llm_or_network`
  greps the module source for forbidden imports.

---

## 4. Migration 021 usage assumptions

This task does NOT apply or alter a migration. It consumes the
columns Codex already applied on
`tourfiremai-v2-staging` (`mbcihtcdwfofagkxphcu`) on 2026-05-20:

| Field consumed                | Migration 021 column   | Legacy mirror |
|-------------------------------|------------------------|---------------|
| `departure_start`             | `departure_start DATE` | `departure_date` |
| `departure_end`               | `departure_end DATE`   | `return_date`  |
| `departure_label_raw`         | `departure_label_raw`  | —             |
| `bus`                         | `bus INTEGER`          | —             |
| `adult_price`                 | `adult_price INTEGER`  | `price`       |
| `child_bed_price`             | `child_bed_price`      | —             |
| `child_no_bed_price`          | `child_no_bed_price`   | —             |
| `single_supplement_price`     | `single_supplement_price` | —          |
| `joinland_price`              | `joinland_price`       | —             |
| `group_size`                  | `group_size INTEGER`   | —             |
| `status_text`                 | `status_text TEXT`     | —             |
| `status_class`                | `status_class TEXT`    | —             |
| `availability_status`         | `availability_status TEXT` (CHECK vocab: `available, limited, sold_out, unknown`) | `status` |
| `source_url`                  | `source_url TEXT`      | —             |
| `tour_code_real`              | `tour_code_real TEXT`  | —             |

`to_tour_departure_rows` (DEV-012) already produces both the new and
the legacy mirror columns in the same payload, and the persistence
helper writes the payload as-is. The legacy `status` mirror defaults to
`"available"` only when the new `availability_status` is `"unknown"` —
this matches the parser's cautious classification of contact-button
rows and is what the existing DEV-012 test
`test_status_mirror_defaults_to_available_when_unknown` already asserts.

The non-unique `idx_dep_full_row (tour_id, departure_start,
departure_end, bus)` index from migration 021 backs the (tour_id,
start, end, bus) idempotency key. The same row of the live HTML will
hash to the same key on every fetch, so the upsert is idempotent
without UNIQUE enforcement at the DB level. The previous QA cycle
flagged tightening this to UNIQUE after a backfill audit (P2-2) — not
in scope here.

The CHECK constraint
`chk_departure_prices_nonneg ((price IS NULL OR price > 0))` is what
makes the "dash stays NULL, never 0" rule load-bearing all the way to
the DB. Our persistence helper does not coerce `None → 0`, so the
constraint can never fire from this code path.

---

## 5. Tests run

All tests run from the repo root, no live network, no live LLM, no
Supabase, no Meta/LINE/OpenAI/OCR provider calls.

### 5.1 Targeted new suite

```
pytest v2/tests/test_detail_enrichment.py \
       v2/tests/test_selected_departure_match.py -v
```

Result: **51 passed in 0.37s**

  - `test_detail_enrichment.py`: 26 tests across `TestBuildDetailUrl`
    (4), `TestFetchDetailHtml` (4), `TestUpsertDepartureRows` (10) and
    `TestEnrichTourDetail` (8).
  - `test_selected_departure_match.py`: 25 tests across
    `TestParseCustomerDatePhrase` (4), `TestMatchDepartureExact` (2),
    `TestMatchDepartureInRange` (1), `TestMatchDepartureNoMatch` (6),
    `TestMatchDepartureAmbiguous` (2), `TestContactButtonNeverSoldOut`
    (2), `TestNoZeroCoercionOnMatch` (1), `TestListAvailableDepartures`
    (4), `TestResultToDict` (3).

### 5.2 Targeted + adjacency suite

```
pytest v2/tests/test_detail_enrichment.py \
       v2/tests/test_selected_departure_match.py \
       v2/tests/test_departure_price_table.py \
       v2/tests/test_scraper.py
```

Result: **143 passed in 0.33s**

Confirms the new modules do not regress the DEV-012 parser (72 tests)
or the Sprint 1 listing scraper (20 tests).

Also re-ran `test_page_post_context.py` and `test_tour_codes.py`
separately as DEV-012 was their shared-helper neighbour — **171 passed,
0 failed** for the 4-file group.

### 5.3 Broad non-live V2 suite

```
pytest v2/tests/ \
  --deselect v2/tests/test_integration_staging.py \
  --deselect v2/tests/test_live_openai_health.py \
  --deselect v2/tests/test_phase2_live_followup.py
```

Result: **783 passed / 47 skipped / 0 failed in 2.72s** (run from the
repo root). Skips are exclusively the flask-only webhook tests and the
cassette/live tests that this hard-rule list excludes.

This is +97 passed over the QA-2026-05-20-012 closing baseline of 686:
the +51 new tests from this package plus +46 the workspace mirror
already had above the prior reported figure (DEV-012 parser at 72 vs.
the older reporting point's 26 — see broader test discovery on the
synced workspace).

### 5.4 First-attempt CWD-dependency note

When the suite was first run from `v2/` (`cd v2 && pytest tests/`),
three pre-existing tests failed because they hard-code a repo-root-
relative path:

- `tests/test_admin_only_preflight.py::test_preflight_json_main_does_not_print_secret_values`
- `tests/test_admin_ops.py::TestNoSecretOrWholesaleLeakage::test_no_secret_pattern_appears_in_module`
- `tests/test_admin_ops.py::TestNoSecretOrWholesaleLeakage::test_no_wholesale_brand_token_appears_in_module`

All three open `"v2/lib/admin_ops.py"` as a literal path or shell out
relative to the repo root. Re-running from the repo root (5.3 command)
makes them green. **No DEV-2026-05-20-013 change is implicated.**
Recommendation for a future hygiene task (out of scope here): switch
those tests to `pathlib.Path(__file__).resolve().parents[...]` so they
are CWD-independent like `TestMigration021Shape` already is.

---

## 6. Risks / assumptions

1. **HTML class-name drift.** The DEV-012 parser depends on
   `.table-dateprice`, `.b-tb-dp`, `.s-tb<n>-n`, and `.b-codepg`. A
   silent rename would yield empty enrichment results. Mitigation is the
   read-only CLI sentinel from DEV-012; not changed here.

2. **Idempotency index is non-unique.** Migration 021 left
   `idx_dep_full_row` non-unique pending a backfill audit (QA-012 P2-2).
   The persistence helper's idempotency key is correct, but a DB-level
   UNIQUE will only become safe to add after the audit + a single one-
   time dedupe. Until then, *application-layer* idempotency is the
   guarantee.

3. **Per-fetch caching is not added.** `enrich_tour_detail` does one
   live HTTP per call. The task explicitly says "fetch detail page only
   when needed for detail enrichment / selected-tour context, not for
   every generic greeting" — the *caller* is responsible for choosing
   when to call this function. A future Phase-6 caching/scheduler task
   should add a TTL or batch refresher.

4. **Past-date threshold is `today`.** `match_departure` rejects any
   parsed date `< today`. If a customer asks during a multi-day
   departure ("4 ส.ค." while a row is 29 ก.ค. – 4 ส.ค.), the in-range
   branch still matches because `today` is `<= departure_end`. This is
   the intended behaviour.

5. **Confidence values are not yet wired to copy.** This task adds
   `confidence ∈ {high, medium, low}` on `DepartureMatch` but does not
   change the response writer. Wiring the copy ("ใช่ทริปวันที่ … ใช่
   มั้ยคะ" for medium/low) is a separate, scoped change to keep
   sales-tone work out of this Dev package per the "do not write
   customer-facing response copy in this task" rule.

6. **Persistence falls back to `web_code`.** If a caller does not pass a
   `tour_id`, the upsert matches on `web_code`. That is correct for the
   admin "preview enrichment" use case but means a single-tour-id row
   collision is possible if two different tours ever shared a web_code.
   The schema and live scraper invariants forbid that today; the test
   `test_no_tour_id_falls_back_to_web_code_match` documents this
   deliberately.

7. **Scraper listing URL still says `/intertourdetail/`.** That field
   is the listing card link stored on `tours_canonical.url`, not the
   detail-read URL. Touching it is out of scope and risks regressing
   live-verified Sprint 1 behaviour. Detail enrichment never reads
   from that field — it builds the URL from `web_code` via
   `build_detail_url()`.

---

## 7. What QA should verify

- The DEV-012 parser is **reused**, not duplicated.
  - `v2/scraper/detail_enrichment.py` imports
    `parse_departure_price_table`, `parse_detail_header_codes`,
    `to_tour_departure_rows`, and `idempotency_key`.
  - No re-implementation of date / money / availability parsing in
    `detail_enrichment.py` or `selected_departure_match.py`.
- Detail reads use `/tour/<web_code>`, never `/intertourdetail/`.
  Greppable on the new files + asserted by
  `test_builds_tour_path_not_intertourdetail`,
  `test_uses_canonical_template`,
  `test_source_url_default_uses_tour_path_not_intertourdetail`,
  `test_fetches_tour_path`, `test_no_live_network_during_unit_run`.
- Persistence is idempotent and non-destructive.
  `test_idempotent_second_run_does_not_duplicate`,
  `test_idempotent_second_enrichment`,
  `test_no_tour_id_falls_back_to_web_code_match`.
- Missing values remain NULL, never zero.
  `test_dash_cells_stay_null_in_persisted_payload`,
  `test_missing_prices_stay_null_in_payload` (DEV-012),
  `test_dash_cells_become_none_on_match`.
- Selected-date matching is deterministic and refuses to guess.
  `test_does_not_guess_low_confidence_by_default`,
  `test_multiple_rows_with_same_start_returns_ambiguous`,
  `test_overlapping_in_range_returns_ambiguous`,
  `test_unparseable_phrase`, `test_past_date_rejected`.
- `web_code`, `tour_code_real`, and `airline` stay separate.
  `test_codes_kept_separate_on_persisted_row`,
  `test_exact_start_date_high_confidence`.
- No customer-facing production behaviour changed. No copy module was
  modified; `response_writer.py` and `orchestrator.py` are untouched.
- No V1, Make.com, production webhook, deploy, or secret changes.
  Confirmed by grep against `app.py`, `webhook_proxy.py`,
  `make_blueprint*.json`, and the `v2/.env*` patterns (none read or
  modified).
- No LLM / OCR / Meta / LINE / OpenAI / paid provider call in tests or
  runtime paths added. Confirmed by
  `test_does_not_use_llm_or_network` and by the persistence/enrichment
  modules importing only `parse_*` helpers and the supabase-like duck
  type.

---

## 8. Next recommended step

1. **Codex commits the four new files** on `v2/s4-followup-vision-
   ondemand` and updates `docs/tasks/TASK_LOG.md` with the
   DEV-2026-05-20-013 result.
2. **QA cycle (QA-2026-05-20-013)** reviews this report and re-runs
   §5.1–§5.3 on a real clone.
3. After QA `GO`, open the next Dev task: **wire
   `enrich_tour_detail` into the orchestrator's tour-selection /
   date-question paths**, and add `selected_departure_match.match_departure`
   into the response writer's pre-LLM planning context (so the LLM
   composes copy *over* the deterministic row, not *of* it).
4. A separate, scoped maintenance task to make the three CWD-dependent
   admin-ops/preflight tests (see §5.4) use
   `Path(__file__).resolve().parents[...]` so a `cd v2 && pytest tests/`
   run is also green.

Stop for QA.
