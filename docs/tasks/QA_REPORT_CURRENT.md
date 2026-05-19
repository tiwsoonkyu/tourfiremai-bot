# QA REPORT - QA-2026-05-20-012

## 1. Verdict

GO_WITH_NOTES

## 2. Source

Owner-reported Claude QA verdict from Tiw:

```text
Verdict: GO_WITH_NOTES
```

The full Claude QA matrix was not committed to this repository at the time of this controller update. This file intentionally records only the owner-reported QA result and does not fabricate detailed QA evidence.

## 3. Scope Reviewed

Dev task under review:

- `DEV-2026-05-20-012`
- Branch: `v2/s4-followup-vision-ondemand`
- Implementation commits:
  - `938f5ef` - detail departure price parser implementation
  - `8663c4a` - task status documentation update

## 4. Controller Evidence Available In Repo

Dev report:

- `docs/tasks/DEV_REPORT_CURRENT.md`

Implemented package:

- `v2/scraper/departure_price_table.py`
- `v2/supabase/migrations/20260520_021_departure_price_rows.sql`
- `v2/tools/live_detail_departure_smoke.py`
- `v2/tests/test_departure_price_table.py`

Dev-reported and Codex-verified tests:

- Targeted parser suite: `76 passed`
- Existing scraper regression: `20 passed`
- Broad non-live V2 suite: `747 passed / 0 failed`
- Read-only live smoke CLI: exit `0` for `ap232919`, `ap242455`, `ap183598`

Live smoke evidence recorded by Codex:

- `ap232919` parsed `tour_code_real=BT-NRT_S15_XJ`, airline `XJ`, first row `2026-06-04` to `2026-06-08`, adult price `20999`.
- `ap242455` parsed `tour_code_real=BCCKG27-HU`, airline `HU`, first row `2026-05-23` to `2026-05-26`, adult price `15998`.
- `ap183598` parsed `tour_code_real=TFUEU0626`, airline unknown/null, first row `2026-06-02` to `2026-06-06`, adult price `17518`.

## 5. Findings

No blocking findings were reported by Tiw from the Claude QA session.

Because the verdict was `GO_WITH_NOTES`, the notes are treated as non-blocking until the detailed QA report is provided. Codex should not invent missing note details.

## 6. Residual Risks

This is not production live approval. Remaining operational gates:

- Migration `20260520_021_departure_price_rows.sql` has not yet been applied to Supabase staging from this controller update.
- The parser is validated as a standalone package; the bot still needs a follow-up task to wire these exact departure rows into scraper/detail enrichment and selected-tour memory.
- The bot still must not confirm seat availability, final price, or booking success from parsed table data.
- Sold-out/full handling must continue to use admin availability overrides, not the generic contact button text from the website table.

## 7. Recommendation / Next Action

Proceed with the next controller stage:

1. Apply migration `20260520_021_departure_price_rows.sql` to V2 Supabase staging only.
2. Open the next Dev task to wire the detail-page departure parser into the V2 scraper/detail enrichment path.
3. Keep V2 disconnected from production Meta webhook and customer-wide traffic until admin-only staging tests pass.

