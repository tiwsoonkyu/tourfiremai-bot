# DEV-2026-05-20-014 — Sprint 5 Package H

## Title

Wire selected departure detail planning into the V2 orchestrator and response writer.

## Status

`PENDING`

## Assigned Role

Claude Dev

## Controller

Codex

## Branch

`v2/s4-followup-vision-ondemand`

## Background

DEV-2026-05-20-012 added the detail-page departure price table parser.

DEV-2026-05-20-013 wired parser output into detail enrichment and selected departure matching:

- `v2/scraper/detail_enrichment.py`
- `v2/lib/selected_departure_match.py`

QA-2026-05-20-013 returned `GO_WITH_NOTES`.

The next user-facing quality gap is that the orchestrator/response layer still does not reliably use the selected departure row after the customer selects a tour/date/pax. The bot must stop forgetting the selected tour and must avoid asking the customer to restart when enough context already exists.

## Business Goal

When a customer has selected a tour and gives a date or passenger count, V2 should use deterministic detail enrichment and departure-row matching before replying.

The bot should:

- lock and reuse the selected tour;
- match the selected departure row when confidence is high;
- use exact row data for planning when available;
- ask a precise clarification when confidence is not high enough;
- never guess prices, fees, status, or availability;
- never mix `web_code`, `tour_code_real`, and airline.

## Scope

Implement this as one integration package. Do not stop after each small subtask unless a P0 risk appears.

### 1. Orchestrator Wiring

Update `v2/lib/orchestrator.py` so the per-turn flow can use detail enrichment and selected departure matching.

Requirements:

- Use existing selected-tour memory/lock if present.
- Resolve the candidate in this priority order:
  1. just-selected tour from the current turn;
  2. existing locked selected tour in memory;
  3. explicit web code or real tour code in customer text;
  4. current detail result if already fetched in the turn;
  5. recent top options only when the customer selects by option number.
- Call detail enrichment only when needed:
  - customer selected a tour;
  - customer gives or asks for a date, passenger count, price, fee, tip, deposit, visa, single supplement, booking summary, or details;
  - customer asks follow-up on the selected tour.
- Do not call detail enrichment on generic greeting, broad search, or country discovery.
- Add a small in-turn or memory-backed guard so the same detail page is not fetched repeatedly for every message.
- Keep all unit tests offline. Network-dependent reads must be mocked.

### 2. Departure Matching

Use `v2/lib/selected_departure_match.py` for customer date phrases and selected tour rows.

Requirements:

- High-confidence exact match: expose selected departure row to response planning.
- Medium/low confidence: ask a concise confirmation question and do not quote exact row price as final.
- No match: show available date choices from the selected tour and ask the customer to choose.
- Past dates must not be treated as valid matches.
- `-` or missing values must remain `None`, never `0`.
- Do not infer sold-out from contact-button text. Availability overrides remain the source of truth for full/sold-out blocking.

### 3. Response Planning

Update the response layer only as needed so it can use selected departure planning data.

Requirements:

- Pass a compact, safe planning object or note into `write_response`.
- Include only fields needed for the answer:
  - selected tour name;
  - `web_code`;
  - `tour_code_real`;
  - airline;
  - departure date range;
  - adult price;
  - child price when present;
  - infant price when present;
  - single supplement when present;
  - joinland when present;
  - group size when present;
  - status text as raw signal only, not final availability.
- If fee data is missing or below policy threshold, keep the current handoff behavior.
- Do not confirm seat availability or final price.
- Do not mention wholesale partner names.

### 4. Tests

Add focused tests for the new integration.

Required cases:

1. Generic greeting or broad country ask does not fetch detail page.
2. Customer selects a tour then asks for details: orchestrator enriches detail once and locks candidate.
3. Customer selects date and pax after selecting tour: high-confidence row is passed to response planning.
4. Customer asks fee/tip/deposit after selected tour: selected tour is not lost.
5. Ambiguous date phrase asks a confirmation instead of guessing.
6. No matching date asks the customer to choose from available dates.
7. `web_code`, `tour_code_real`, and airline stay separate in planning.
8. Missing row values remain `None`, not `0`.
9. Sold-out/full overrides still block the candidate before LLM response.
10. No live network, OpenAI, LINE, Meta, OCR, or Supabase calls in unit tests.

Run at minimum:

```bash
pytest v2/tests/test_orchestrator_planning.py v2/tests/test_detail_enrichment.py v2/tests/test_selected_departure_match.py -q
pytest v2/tests --ignore=integration --ignore=live -q
```

If Windows temp permissions fail, rerun with:

```bash
pytest v2/tests --ignore=integration --ignore=live --basetemp=.pytest_tmp -p no:cacheprovider -q
```

## Out of Scope

- Do not deploy.
- Do not modify V1.
- Do not modify Make.com.
- Do not change production Meta webhook settings.
- Do not apply Supabase migrations.
- Do not add live paid provider calls.
- Do not implement real customer-wide traffic.
- Do not build the dashboard UI in this task.
- Do not implement new OCR providers in this task.

## Deliverables

- V2-only code/tests.
- `docs/tasks/DEV_REPORT_CURRENT.md`
- `docs/tasks/AGENT_STATUS.json`

## Required Dev Report

Write `docs/tasks/DEV_REPORT_CURRENT.md` with:

1. Summary
2. Files changed
3. Implementation details
4. Test results
5. Safety / scope guard verification
6. Known notes / risks
7. Exact QA focus areas
8. Recommendation: `GO`, `GO_WITH_NOTES`, or `NO_GO`

Update `docs/tasks/AGENT_STATUS.json` to:

- `status`: `READY_FOR_QA`
- `current_dev_task`: `DEV-2026-05-20-014`
- `current_qa_task`: `QA-2026-05-20-014`
- `next_action`: `CLAUDE_QA_RUN_CURRENT_QA_TASK`

Then stop.
