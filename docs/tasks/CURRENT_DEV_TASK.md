# CURRENT DEV TASK

## Task ID
DEV-2026-05-20-012

## Title
Sprint 5 Package F - Detail Page Departure Price Table Parser

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

## Business Context

Customer-facing sales quality currently depends on the bot being able to read exact departure rows and prices from each tour detail page.

Listing pages are useful for Top 3 discovery, but they are not enough for booking-stage answers because:

- departure prices can differ by date;
- single supplement, joinland, child prices, group size, and status appear in the detail page price table;
- customer date selection must lock an exact departure row before any booking summary;
- sold-out / full status may come from admin overrides, not necessarily from the website row status.

## Goal

Build a deterministic parser for `tourfiremai.com/tour/<web_code>` detail pages that extracts the per-departure price table into structured rows.

This is a foundation task only. It must not change production traffic, customer webhook behavior, or final sales recommendation logic unless explicitly scoped below.

## Live HTML Findings To Preserve

- Correct detail URL pattern is `/tour/<web_code>`.
- Older `/intertourdetail/<web_code>` returned HTTP 500 in live checks and should not be used for new V2 code.
- The price table is server-rendered HTML and does not require a headless browser.
- The layout is div-based, not a normal `<table>`.
- Known structure:
  - `div.table-dateprice`
  - row wrapper: `div.b-tb-dp`
  - cells: `s-tb1-n` through `s-tb9-n`
- `-` means "missing / not provided" and must be stored as `NULL`, never `0`.
- Status cell often contains only the LINE/contact button text such as `ติดต่อเจ้าหน้า`; parser should preserve raw text and class signals but must not treat this as sold-out.
- Sold-out / full should be controlled by `tour_availability_overrides` from migration 020 when present.
- `tour_code_real` appears in the page header near `.b-codepg` and must be kept separate from:
  - `web_code` such as `ap232919`
  - airline such as `XJ` or `VZ`

## Scope

### 1. Add Detail Departure Parser

Create a V2-only module, preferred location:

- `v2/scraper/departure_price_table.py`

Implement pure parsing helpers with no DB writes and no network by default:

- `DeparturePriceRow` dataclass or typed dict
- `parse_departure_price_table(html: str, web_code: str, source_url: str | None = None) -> list[DeparturePriceRow]`
- `parse_detail_header_codes(html: str) -> dict`

Each row should capture at minimum:

- `web_code`
- `tour_code_real`
- `departure_start`
- `departure_end`
- `departure_label_raw`
- `bus`
- `adult_price`
- `child_bed_price`
- `child_no_bed_price` if present / derivable
- `single_supplement_price`
- `joinland_price`
- `group_size`
- `status_text`
- `status_class`
- `availability_status`
- `source_url`

Parser rules:

- Thai Buddhist Era year such as `69` must parse to Gregorian `2026`.
- Thai month abbreviations must parse correctly, including May, June, July, and August abbreviations as they appear on the live website.
- Date ranges crossing months must parse correctly, e.g. a late-July to early-August Buddhist Era year suffix range.
- Money strings with comma parse to integers.
- `-`, empty string, or non-price placeholders parse to `None`.
- Do not infer sold-out from generic contact text.
- Do not call LLM, OpenAI, OCR, Meta, LINE, Supabase, or external APIs from parser tests.

### 2. Add Schema Migration

Add a new additive migration:

- `v2/supabase/migrations/20260520_021_departure_price_rows.sql`

Preferred approach: extend `tour_departures` in a backward-compatible way.

Add columns if missing:

- `departure_start DATE`
- `departure_end DATE`
- `departure_label_raw TEXT`
- `bus INTEGER`
- `adult_price INTEGER`
- `child_bed_price INTEGER`
- `child_no_bed_price INTEGER`
- `single_supplement_price INTEGER`
- `joinland_price INTEGER`
- `group_size INTEGER`
- `status_text TEXT`
- `status_class TEXT`
- `availability_status TEXT`
- `source_url TEXT`

Compatibility mapping:

- Existing `departure_date` may mirror `departure_start`.
- Existing `return_date` may mirror `departure_end`.
- Existing `price` may mirror `adult_price`.

Do not apply the migration in this Dev task.

### 3. Add Upsert Helper Or Dry-Run Adapter

Add a deterministic adapter function that converts parsed rows into the existing `tour_departures` shape.

Preferred locations:

- same parser module, or
- `v2/scraper/scrape_tours.py` if it already owns upsert logic.

Rules:

- Must be idempotent by `(tour_id or web_code, departure_start, departure_end, bus)` where possible.
- Must not destroy existing rows.
- Must not map missing prices to zero.
- Must preserve exact row price for future selected-date locking.

### 4. Add Read-Only Live Smoke CLI

Add an optional CLI that fetches 1-3 real detail pages and prints a redacted structured summary.

Preferred location:

- `v2/tools/live_detail_departure_smoke.py`

Rules:

- Read-only.
- No DB write.
- No LLM.
- No secrets.
- Default sample web codes may include `ap232919`, `ap242455`, `ap183598`.
- Output must be compact and safe for Dev reports.

### 5. Tests

Add targeted tests covering:

- parse rows from saved HTML fixture snippets;
- parse Thai date ranges with same month, different month, and year suffix;
- parse money values and `-` as `None`;
- extract `tour_code_real` separately from `web_code` and airline;
- status text is preserved but not treated as sold-out;
- migration SQL contains all expected additive columns;
- adapter maps `adult_price` to legacy `price` without losing detailed price fields.

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
- change customer-facing response copy;
- change fee extraction thresholds;
- change admin dashboard routes;
- mark tours sold-out based only on website contact button text.

## Expected Deliverables

- New parser module and tests.
- Migration 021 SQL file.
- Optional read-only live smoke CLI.
- Updated docs if needed.
- `docs/tasks/DEV_REPORT_CURRENT.md`.
- `docs/tasks/AGENT_STATUS.json`.

## Required Dev Report

Write `docs/tasks/DEV_REPORT_CURRENT.md` with:

1. Status
2. Files changed
3. Summary of changes
4. Live HTML assumptions verified
5. Tests run
6. Risks / assumptions
7. What QA should verify
8. Next recommended step

Then stop for QA.

## What QA Should Verify

QA should verify:

- parser is deterministic and does not use LLM/OCR/network in unit tests;
- `/tour/<web_code>` is used for future detail reads, not `/intertourdetail/<web_code>`;
- `web_code`, `tour_code_real`, and airline are not mixed;
- `-` is stored as `NULL`, not `0`;
- row-level adult price and single supplement are captured correctly;
- status/contact text is not interpreted as sold-out;
- migration 021 is additive and backward-compatible;
- no V1, Make.com, production webhook, deploy, or secret changes.


