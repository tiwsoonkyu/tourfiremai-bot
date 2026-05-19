# DEV REPORT — DEV-2026-05-20-012

## Task
Sprint 5 Package F — Detail Page Departure Price Table Parser.

Source of truth read at task start:
- Repo: `github.com/tiwsoonkyu/tourfiremai-bot`
- Branch: `v2/s4-followup-vision-ondemand`
- Base commit: `a9ed2ea` (`docs(tasks): open detail price table parser task`)
- Task spec: `docs/tasks/CURRENT_DEV_TASK.md`
- Controller config: `docs/AI_COMMAND_CENTER.md`

## 1. Status
`READY_FOR_QA`

All five scope items in `CURRENT_DEV_TASK.md` are delivered as a deterministic,
read-only parsing package. No production traffic, customer outbound copy, V1
code, Make.com scenario, Supabase migration apply, or paid-provider call was
touched.

Codex follow-up before QA: copied the Cowork deliverables into the real git
checkout, ran the read-only live smoke CLI against three production detail
pages, found two live-HTML parser gaps, and fixed them before handoff:

- full two-sided date ranges such as `04 มิ.ย. 69 - 08 มิ.ย. 69` now parse
  start/end correctly;
- live `.b-codepg > .txt-pd-l` values such as `BT-NRT_S15_XJ` are preferred
  over label/title text, and airline `XJ` is derived from dash/underscore
  suffix tokens.

## 2. Files changed

New files only — no modifications to existing source.

- `v2/scraper/departure_price_table.py` — deterministic detail-page parser.
- `v2/supabase/migrations/20260520_021_departure_price_rows.sql` — additive,
  idempotent migration extending `tour_departures`. Not applied by this task.
- `v2/tools/live_detail_departure_smoke.py` — optional read-only CLI for live
  smoke checks. No DB write, no LLM, no secrets.
- `v2/tests/test_departure_price_table.py` — 76 unit tests covering parsing,
  classification, adapter, idempotency key, and migration shape.

No edits to `v2/lib/`, `v2/webhook/`, `v2/scraper/scrape_tours.py`,
`v2/lib/orchestrator.py`, `v2/lib/response_writer.py`, V1 `app.py`, V1
Make.com blueprints, or any production webhook/secret file.

## 3. Summary of changes

### 3.1 Parser module — `v2/scraper/departure_price_table.py`

Public API (per task spec):

- `DeparturePriceRow` dataclass — holds web_code, tour_code_real,
  departure_start, departure_end, departure_label_raw, bus, adult_price,
  child_bed_price, child_no_bed_price, single_supplement_price, joinland_price,
  group_size, status_text, status_class, availability_status, source_url,
  airline, plus `raw_cells` for QA visibility. `to_dict()` ISO-serialises dates.
- `parse_departure_price_table(html, web_code, source_url=None) -> list[DeparturePriceRow]`
  — splits the page's `div.table-dateprice` block, iterates `div.b-tb-dp`
  rows, extracts `s-tb1-n` .. `s-tb9-n` cells, parses each row.
- `parse_detail_header_codes(html) -> dict` — returns
  `{tour_code_real, airline, web_code}`. Each field independent; never merged.
- `parse_thai_date_range(text, year_hint=None)` — Thai-locale date parser
  covering same-month, cross-month, cross-year, and single-date forms, with
  Buddhist Era → Gregorian conversion (`69` → `2026`).
- `to_tour_departure_rows(rows, tour_id=None)` — adapter producing
  `tour_departures`-shaped dicts with legacy mirrors
  (`departure_date`/`return_date`/`price`) and new detailed columns.
- `idempotency_key(payload, tour_id=None)` — returns
  `(tour_id-or-web_code, departure_start, departure_end, bus)` for upsert dedup.

Determinism guarantees:

- Pure regex parsing over a string. No network, no DB, no LLM/OCR import.
- `_parse_money_or_none` rejects `"0"`, `"-"`, `""`, "ติดต่อเจ้าหน้าที่",
  and any cell without a digit. `"25,900 บาท"` → `25900`.
- `_classify_availability` returns `unknown` for contact-button copy; only a
  `sold-out` / `full` / `closed` / `เต็ม` class fragment on the row or cell
  flips it to `sold_out`. Explicit `ว่าง / available / open` text → `available`.
- Source URL defaults to `BASE_URL + "/tour/<web_code>"`. The old
  `/intertourdetail/<web_code>` path (returned HTTP 500 in live checks) is
  never produced by this module.

### 3.2 Migration 021 — `v2/supabase/migrations/20260520_021_departure_price_rows.sql`

Additive only. Every column uses `ADD COLUMN IF NOT EXISTS`; constraints are
wrapped in `DO $$ BEGIN … EXCEPTION WHEN duplicate_object THEN NULL; END $$`
so re-running is safe. New columns mirror the dataclass fields:

`departure_start`, `departure_end`, `departure_label_raw`, `bus`,
`adult_price`, `child_bed_price`, `child_no_bed_price`,
`single_supplement_price`, `joinland_price`, `group_size`, `status_text`,
`status_class`, `availability_status`, `source_url`, `tour_code_real`.

Backfill `UPDATE`s mirror legacy → new only where new is NULL, never the
other direction, so already-populated detail data is never overwritten.

New `CHECK` constraints:

- All price columns must be NULL or > 0 (locks the "never silently 0" rule).
- `departure_end >= departure_start` when both present.
- `availability_status ∈ {available, limited, sold_out, unknown}`.
- `bus`, `group_size` NULL or > 0.

New indexes: `idx_dep_start`, `idx_dep_web_code_start`, `idx_dep_full_row`.
No `DROP COLUMN`, no `RENAME COLUMN`, no `DROP TABLE`, no `TRUNCATE`. This
migration is NOT applied by Claude Dev (hard rule).

### 3.3 Adapter behaviour

`to_tour_departure_rows`:

- Skips rows where `departure_start is None` (would violate the existing
  unique index on `tour_departures`).
- Mirrors `departure_start → departure_date`, `departure_end → return_date`,
  `adult_price → price` so pre-migration reads keep working.
- Never coerces `None → 0`. Missing prices remain `null` in the payload.
- Sets legacy `status` cautiously: `unknown → available` (cautious default
  until `tour_availability_overrides` flips it), `sold_out → sold_out`.

### 3.4 Live smoke CLI — `v2/tools/live_detail_departure_smoke.py`

Read-only. `python -m v2.tools.live_detail_departure_smoke` defaults to
`ap232919`, `ap242455`, `ap183598`. Emits one JSON line per code with
`row_count`, first-five-row summaries, header codes, and a redacted
`status_text_sample` (40 chars). No DB write, no LLM, no secrets, no Meta /
LINE / OpenAI / OCR / paid-provider call. Exits non-zero on HTTP error or
zero-row parse so a CI smoke can flag drift.

## 4. Live HTML assumptions verified (from `CURRENT_DEV_TASK.md`)

| Assumption | Encoded in |
|---|---|
| `/tour/<web_code>` is the correct detail URL | `BASE_URL + DETAIL_PATH`; CLI; `test_source_url_default_uses_tour_path_not_intertourdetail` |
| Container `div.table-dateprice` | `_TABLE_BLOCK_RE` |
| Row wrapper `div.b-tb-dp` | `_ROW_BLOCK_RE` |
| Cells `s-tb1-n` .. `s-tb9-n` | `_CELL_BLOCK_RE`; fixture & full-row test |
| `-` ≠ 0 | `_parse_money_or_none`, multiple `test_missing_tokens_return_none_not_zero` cases |
| Contact button text is not sold-out | `_classify_availability` + `test_contact_button_is_unknown_not_sold_out` + `test_contact_button_status_never_becomes_sold_out` |
| Sold-out from class signal / overrides only | `SOLD_OUT_CLASS_FRAGMENTS`; row-soldout fixture row |
| `tour_code_real` ≠ `web_code` ≠ `airline` | `parse_detail_header_codes`; `test_extracts_tour_code_real_airline_web_code_separately` |
| Thai BE 2-digit year → Gregorian | `_resolve_year`; `test_*_be_year_suffix` |

The detail HTML used in tests is a synthetic fixture (no live fetch in unit
tests). The CLI is the deliberate channel for any future live verification.

## 5. Tests run

Environment: Windows local git checkout, `.venv_codex`, pytest 9.0.3.

Targeted new suite:

```
.\.venv_codex\Scripts\python.exe -m pytest v2\tests\test_departure_price_table.py -q -p no:cacheprovider --basetemp=.pytest_tmp
=> 76 passed in 0.31s
```

Existing scraper regression:

```
.\.venv_codex\Scripts\python.exe -m pytest v2\tests\test_scraper.py -q
=> 20 passed in 0.06s
```

Broad non-live V2 suite (excluding env-gated live integration tests):

```
.\.venv_codex\Scripts\python.exe -m pytest v2\tests \
  --ignore=v2\tests\test_integration_staging.py \
  --ignore=v2\tests\test_live_openai_health.py \
  --ignore=v2\tests\test_phase2_live_followup.py \
  -q -p no:cacheprovider --basetemp=.pytest_tmp
=> 747 passed, 0 failed in 12.48s
```

CLI smoke (no network, just argparse import):

```
PYTHONPATH=. python3 -m v2.tools.live_detail_departure_smoke --help
=> exit 0, usage banner shown, parser module imports cleanly
```

Read-only live smoke against production detail pages:

```
.\.venv_codex\Scripts\python.exe -m v2.tools.live_detail_departure_smoke ap232919 ap242455 ap183598
=> exit 0, 3/3 ok
```

Live smoke evidence:

| web_code | parsed tour_code_real | airline | row_count | first parsed row |
|---|---|---:|---:|---|
| `ap232919` | `BT-NRT_S15_XJ` | `XJ` | 15 | `04 มิ.ย. 69 - 08 มิ.ย. 69` → `2026-06-04` to `2026-06-08`, adult `20,999` |
| `ap242455` | `BCCKG27-HU` | `HU` | 7 | `23 พ.ค. 69 - 26 พ.ค. 69` → `2026-05-23` to `2026-05-26`, adult `15,998` |
| `ap183598` | `TFUEU0626` | `null` | 17 | `02 มิ.ย. 69 - 06 มิ.ย. 69` → `2026-06-02` to `2026-06-06`, adult `17,518` |

The live smoke is read-only HTTP GET only. It does not touch Supabase, Meta,
LINE, OpenAI, OCR, or any customer-facing channel.

## 6. Risks / assumptions

1. **HTML drift.** The parser is regex-based against the live class names
   (`table-dateprice`, `b-tb-dp`, `s-tb1-n`..`s-tb9-n`, `b-codepg`). If
   tourfiremai.com renames these, the parser returns `[]` rather than wrong
   data. The smoke CLI exits non-zero on zero rows so drift is observable.
2. **`limited` availability** is in the SQL CHECK constraint vocabulary but
   the parser only emits `available`, `sold_out`, `unknown`. `limited` is
   reserved for `tour_availability_overrides` / admin signals.
3. **Cross-year date heuristic.** A range like "29 ธ.ค. - 4 ม.ค. 69" assumes
   the start month is the previous year when start-month > end-month. This is
   correct for normal travel ranges but would mis-handle a deliberately
   forward-dated 12-month range. No such row has been observed in live HTML.
4. **`tour_code_real` regex** matches ALLCAPS tokens with optional dashes. A
   future format like `JPN6DJL` (no dash) is still matched. A future format
   with lowercase letters would not be — this is intentional to avoid eating
   web_codes.
5. **Adapter does not write.** The adapter shapes payloads but does NOT
   perform the upsert. Wiring it into the cron entrypoint is out of scope and
   tracked for the next package.

## 7. What QA should verify

Per `CURRENT_DEV_TASK.md § "What QA Should Verify"`:

- [ ] Parser is deterministic. No LLM/OCR/network in unit tests — confirm by
  running `pytest v2/tests/test_departure_price_table.py -v` and grepping the
  module for any `import openai|anthropic|requests` (only the CLI imports
  `requests` lazily inside `_fetch`).
- [ ] `/tour/<web_code>` is the canonical detail URL — confirm
  `BASE_URL + DETAIL_PATH` and that `/intertourdetail/` does not appear in
  `departure_price_table.py` outputs.
- [ ] `web_code`, `tour_code_real`, and `airline` remain three separate
  fields — confirm
  `test_extracts_tour_code_real_airline_web_code_separately` and the
  fixture's combination `ap242455` / `BCCKG27-HU` / `HU`.
- [ ] `"-"` cells become `NULL`, never `0` — confirm
  `test_dash_cells_yield_none_not_zero`,
  `test_missing_tokens_return_none_not_zero`,
  `test_missing_prices_stay_null_in_payload`, and the migration's
  `chk_departure_prices_nonneg` constraint.
- [ ] Adult price and single supplement captured per row — confirm
  `test_first_row_full_field_extraction`.
- [ ] Status / contact text never interpreted as sold-out — confirm
  `test_contact_button_is_unknown_not_sold_out`,
  `test_contact_button_status_never_becomes_sold_out`. Sold-out only via
  `row-soldout` / `full` / `closed` / `เต็ม` class fragments.
- [ ] Migration 021 is additive, no `DROP COLUMN`/`RENAME COLUMN`/
  `DROP TABLE`/`TRUNCATE` — confirm `TestMigration021Shape` 19 cases.
- [ ] No V1 (`app.py`, `webhook_proxy.py`), Make.com (`make_blueprint*.json`),
  production webhook, deploy, or secret file was modified — confirm via diff
  scoping; only the four files listed in §2 are new.
- [ ] No live LLM/OCR/Meta/LINE/OpenAI/paid-provider/Supabase-migration-apply
  call is reachable from the parser tests — confirm by inspecting the test
  file imports.

## 8. Next recommended step

1. Hand `QA-2026-05-20-012` to Claude QA against this Dev report and the
   committed branch diff.
2. After `GO`/`GO_WITH_NOTES`, the migration can be applied on staging via the
   normal staging migration pipeline (still NOT in Dev's scope).
3. A follow-up package should wire the adapter into the listing scraper's
   detail-fetch path and add a Top-3 → exact-row lock-in flow for
   `selected_tours`.
