# CURRENT DEV TASK

## Task ID
DEV-2026-05-20-013

## Title
Sprint 5 Package G - Wire Detail Departure Rows Into Scraper and Selected-Tour Memory

## Status
PENDING

## Assigned Role
Claude Dev

## Controller
Codex

## Source of Truth

Use GitHub repo as source of truth:

- Repo: `github.com/tiwsoonkyu/tourfiremai-bot`
- Branch: `v2/s4-followup-vision-ondemand`

If local workspace lacks git/source files, differs from GitHub branch, or cannot inspect the changed files, stop and report:

`BLOCKED: source-of-truth repo unavailable`

Do not invent scope from chat memory. This file is the approved scope for the current Dev task.

## Context

DEV-2026-05-20-012 created and QA-cleared the deterministic `/tour/<web_code>` detail-page parser. Codex applied and verified staging migration `20260520_021_departure_price_rows.sql` on V2 Supabase staging (`tourfiremai-v2-staging`) before opening this task.

The next sales-quality gap is wiring those parsed departure rows into the V2 scraper/detail enrichment path and selected-tour memory, so the bot can later answer exact row-level date/price questions without losing context or mixing codes.

## Business Goal

When a customer selects a tour and/or asks for a specific departure date, V2 must have deterministic row-level data available:

- exact departure start/end;
- adult / child / single supplement / joinland / group size where present;
- web code vs real tour code vs airline kept separate;
- no sold-out inference from generic website contact text;
- no final seat or final price confirmation.

## Scope

### 1. Wire Parser Into Scraper / Detail Enrichment

Integrate `v2/scraper/departure_price_table.py` into the V2 scraper/detail flow.

Preferred locations:

- `v2/scraper/scrape_tours.py`
- or a small V2-only helper module if that keeps the existing scraper cleaner.

Required behavior:

- Use `/tour/<web_code>` only for detail-page reads.
- Do not use `/intertourdetail/<web_code>`.
- Fetch detail page only when needed for detail enrichment / selected-tour context, not for every generic greeting.
- Parse and map departure rows using the DEV-012 parser.
- Preserve `web_code`, `tour_code_real`, and `airline` as distinct fields.

### 2. Persist Parsed Rows To `tour_departures`

Add an idempotent persistence helper for parsed detail rows.

Required behavior:

- Use migration 021 columns already applied on staging.
- Do not destroy existing rows.
- Do not map missing values to zero.
- Prefer idempotent matching on `(tour_id or web_code, departure_start, departure_end, bus)` where possible.
- Mirror compatibility fields where needed:
  - `departure_start` -> legacy `departure_date`
  - `departure_end` -> legacy `return_date`
  - `adult_price` -> legacy `price`
- Preserve detailed fields:
  - `departure_label_raw`
  - `bus`
  - `adult_price`
  - `child_bed_price`
  - `child_no_bed_price`
  - `single_supplement_price`
  - `joinland_price`
  - `group_size`
  - `status_text`
  - `status_class`
  - `availability_status`
  - `source_url`
  - `tour_code_real`

### 3. Selected-Tour Memory / Offer Snapshot Readiness

Add deterministic data structures or helpers so a selected tour can carry row-level departure options forward.

Required behavior:

- When a customer selects "ตัวที่ 1", web code, real tour code, price, or name, the selected tour context should be able to include parsed departure rows.
- If the customer then says a date such as "13 มิ.ย. 3 คน", helper logic should identify the matching departure row or return an explicit low-confidence / no-match result.
- Do not write customer-facing response copy in this task unless it is test-only or internal planning data.
- Do not make the LLM the source of truth for selected row matching.

### 4. Tests

Add targeted tests covering:

- detail page parser is called from the scraper/detail enrichment path;
- parsed rows upsert/mapping preserves detailed fields;
- `-` stays `None` / SQL NULL, never `0`;
- `web_code`, `tour_code_real`, and airline stay separate;
- selected-date matching finds the correct departure row;
- selected-date matching returns no-match instead of guessing;
- generic contact/status text is not treated as sold-out;
- no live network calls in unit tests;
- no V1 / Make.com / production webhook changes.

Run:

- targeted new tests;
- relevant scraper tests;
- broad non-live V2 suite when feasible.

## Out of Scope

Do not:

- touch V1 production behavior;
- modify Make.com;
- change production Messenger webhook settings;
- deploy anything;
- apply Supabase migrations;
- read or print secrets;
- call OpenAI, OCR, LINE, Meta, or paid providers;
- enable customer-wide traffic;
- confirm seat availability, final price, payment, or booking success;
- infer sold-out/full from website contact button text;
- change fee extraction confidence thresholds.

## Expected Deliverables

- V2-only scraper/detail enrichment code.
- V2-only selected-tour row matching / memory helper code.
- Tests.
- Updated docs if needed.
- `docs/tasks/DEV_REPORT_CURRENT.md`.
- `docs/tasks/AGENT_STATUS.json`.

## Required Dev Report

Write `docs/tasks/DEV_REPORT_CURRENT.md` with:

1. Status
2. Files changed
3. Summary of changes
4. Migration 021 usage assumptions
5. Tests run
6. Risks / assumptions
7. What QA should verify
8. Next recommended step

Then stop for QA.

## What QA Should Verify

QA should verify:

- DEV-012 parser is reused, not duplicated;
- detail reads use `/tour/<web_code>`, not `/intertourdetail/<web_code>`;
- row persistence is idempotent and non-destructive;
- missing values remain null, never zero;
- selected-date matching is deterministic and refuses to guess;
- real tour code, web code, and airline are not mixed;
- no customer-facing production behavior changed;
- no V1, Make.com, production webhook, deploy, or secret changes.

