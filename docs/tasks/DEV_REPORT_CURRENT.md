# Dev Report — `DEV-2026-05-19-003` cleanup (QA L1 + L2)

**Status:** `READY_FOR_QA`
**Verdict recommendation to QA:** **`GO`**
**Author:** Claude Cowork Dev
**Date:** 2026-05-19
**Branch:** `v2/s4-followup-vision-ondemand` (pushed)
**HEAD commit:** `e473a26` (parent: `bd4784e`)
**Spend this session:** **$0.00** — no live paid call, no live OpenAI call, no tests required credentials.

---

## 1. Status

`READY_FOR_QA`. QA returned `QA_GO` on the parent commits with two informational notes (L1, L2). Both are now closed in a single tiny cleanup commit; full unit suite goes 495 → 501 / 0 failures.

---

## 2. Files Changed (on `v2/s4-followup-vision-ondemand` @ `e473a26`)

```
v2/scraper/document_parser_provider.py    |  24 ++  (L1 — explicit _CONF_COL map)
v2/scraper/run_fee_pipeline.py            |   8 +-  (L2 — needs_supabase predicate)
v2/tests/test_document_parser_provider.py | 150 ++  (6 new regression tests)
3 files changed, 179 insertions(+), 3 deletions(-)
```

**NOT changed:** any V1 path, any prompt, any migration, any production env, any secret store, any Make.com module, any Meta webhook code, any deploy setting, `fee_answer_policy.py` (thresholds unchanged), the orchestrator runtime path, `extract_fees_on_demand`, `ondemand_vision.py`, `extract_fees.py`. Verified via `git diff bd4784e..e473a26 --name-only` → only the 3 files listed above.

---

## 3. Summary of Changes

### L1 — MockDocumentParser confidence-column mapping (`v2/scraper/document_parser_provider.py`)

**Before:**
```python
confs = {f"{k.split('_')[0]}_confidence": (0.85 if v is not None else None)
          for k, v in fee_fields.items() if v is not None}
```

This shorthand produced `single_confidence` for `single_supplement` and `visa_confidence` for `visa_fee` only coincidentally. The grader's `_parse_result_to_extraction` looks for the exact column names `single_supplement_confidence` / `visa_confidence` (matching `ExtractionResult` field names and the `tour_fees` table columns), so the mock provider's `single_supplement_confidence` was silently dropped during benchmark grading.

QA flagged this as "safer-side" (mock alone couldn't answer single_supplement at 0.85 < policy 0.90 anyway), but the benchmark numbers were misleading.

**After:**
```python
_CONF_COL = {
    "tip_amount":         "tip_confidence",
    "deposit_amount":     "deposit_confidence",
    "single_supplement":  "single_supplement_confidence",
    "visa_fee":           "visa_confidence",
    # infant_fee + child_fee_no_bed have no per-field confidence column
    # in tour_fees — intentionally omitted; grader falls back to
    # row-level extraction_confidence.
}
confs = {
    _CONF_COL[k]: 0.85
    for k, v in fee_fields.items()
    if v is not None and k in _CONF_COL
}
```

The benchmark grader now actually sees the mock's per-field confidences.

### L2 — Supabase init skipped for benchmark-only / corpus-only modes (`v2/scraper/run_fee_pipeline.py`)

**Before:**
```python
supabase = make_supabase_from_config(config) if not args.pdf_corpus else None
```

This initialized Supabase for `--pdf-corpus-ondemand` AND `--benchmark-providers` even though both paths read only fixture PDFs + ground-truth JSON from disk and never touch `tour_fees`. Result: an operator running a pure mock benchmark would still need to populate `V2_STAGING_DB_PASSWORD` etc.

**After:**
```python
needs_supabase = not (
    args.pdf_corpus
    or args.pdf_corpus_ondemand
    or args.benchmark_providers
)
supabase = make_supabase_from_config(config) if needs_supabase else None
```

Mock benchmark + on-demand corpus + plain corpus now run without requiring a live DB connection. Single-tour / web-code / `--all` invocations still init Supabase as before — no change to that code path.

---

## 4. Tests Run

```
PYTHONPATH=. pytest v2/tests/test_document_parser_provider.py -x
# 32 passed in 0.16s   (26 prior + 6 new for L1/L2)

PYTHONPATH=. pytest v2/tests --ignore=v2/tests/test_integration_staging.py \
                              --ignore=v2/tests/test_live_openai_health.py -q
# 501 passed in 2.48s — 0 failures, 0 regressions
```

### New tests (6 in `TestQACleanupL1ConfidenceKeys` + `TestQACleanupL2NoSupabaseInBenchmarkMode`)

**L1 (4 tests):**
- `test_single_supplement_maps_to_correct_column` — was the bug; asserts `single_supplement_confidence` is present AND the old `single_confidence` is absent.
- `test_deposit_maps_to_correct_column` — was correct by coincidence; locked in.
- `test_tip_visa_keys_match_extractionresult` — every emitted key matches an `ExtractionResult` field name; no stragglers.
- `test_benchmark_grader_sees_single_supplement_confidence` — end-to-end via `_parse_result_to_extraction`: the converted ExtractionResult carries `single_supplement_confidence == 0.85` (previously None).

**L2 (2 tests):**
- `test_benchmark_providers_does_not_call_make_supabase` — spies on `make_supabase_from_config`; asserts `called == []` when `main(["--benchmark-providers", "mock"])` runs against a tmp fixture tree.
- `test_pdf_corpus_ondemand_also_skips_supabase` — same spy pattern for `--pdf-corpus-ondemand`; preserves the existing skip behavior under the unified predicate.

### Anti-regression cross-checks

- `TestFeePolicyUnchanged::test_low_confidence_single_supplement_still_handsoff` — still passing; the L1 fix doesn't change the mock's 0.85 cap, so policy still routes single_supplement to handoff.
- `TestPaidStubsFailClosed` — still passing on all 3 paid stubs; the L1/L2 changes are localized.

---

## 5. Risks / Assumptions

### Assumptions
1. QA's L1 and L2 are exactly as documented in the QA report and the AGENT_STATUS.json `notes` field; no hidden third note.
2. The benchmark grader's behavior on partial confidences is unchanged — when a confidence column is None, it falls back to row-level `extraction_confidence`. This matches the pre-L1 behavior for the rows that previously dropped through.

### Risks (carried forward, not introduced)
- **R1.** Real-corpus accuracy on Phase 2 is still unmeasured against the latest branch. Operational; out of scope here.
- **R2.** The benchmark cost-rollup uses `unpriced_calls` accounting; once a real paid provider is wired (e.g. Mistral OCR), its pricing entry must be added to `v2/lib/llm_pricing.MODEL_PRICING_USD_PER_TOKEN` (or a sibling per-page pricing table). Not in scope for this cleanup.

### Hard rules — all respected
- ✅ V1 untouched.
- ✅ Make.com untouched.
- ✅ No deploy.
- ✅ No live OpenAI in tests.
- ✅ No live paid-provider call.
- ✅ Fee thresholds in `fee_answer_policy.py` UNCHANGED (verified by `git diff bd4784e..e473a26 -- v2/lib/fee_answer_policy.py` → empty).
- ✅ No production runtime behavior change — only the mock provider's emitted key names and the CLI-side Supabase-init predicate moved.
- ✅ No secret printed/written/committed (pre-commit grep clean; PAT only in transient push URL; `.git/config` token-free post-push).
- ✅ No wholesale brand names introduced (new test code uses the same scoped blacklist regex as the parent commit).

---

## 6. What QA Should Verify

| # | Check | How to verify |
|---|-------|---------------|
| 1 | Diff is tiny + test-only edits stay in tests | `git diff bd4784e..e473a26 --stat` → 3 files, +179/-3 |
| 2 | L1 fix is correct | `TestQACleanupL1ConfidenceKeys` (4 cases) |
| 3 | L2 fix is correct | `TestQACleanupL2NoSupabaseInBenchmarkMode` (2 cases) |
| 4 | No regression on parent DEV-2026-05-19-003 tests | `pytest v2/tests/test_document_parser_provider.py` → 32 pass |
| 5 | Anti-guess invariant still holds | `TestFeePolicyUnchanged` still passes |
| 6 | `fee_answer_policy.py` thresholds unchanged | `git diff bd4784e..e473a26 -- v2/lib/fee_answer_policy.py` → empty |
| 7 | No runtime fee-answer behavior change | `git diff bd4784e..e473a26 -- v2/lib/orchestrator.py v2/lib/response_writer.py v2/scraper/extract_fees.py v2/scraper/ondemand_vision.py` → empty |
| 8 | Full suite | `pytest v2/tests --ignore=integration --ignore=live -q` → 501 passed |

---

## 7. Next Recommended Step

**For Codex:** Accept this `GO` verdict. The Phase 2 follow-up workstream on `v2/s4-followup-vision-ondemand` is now at a clean state — `e473a26`. Two natural next options:

1. **Real Mistral OCR implementation** — fill in `MistralOCRParser.parse()` using the `mistralai` SDK + per-page pricing entry in `v2/lib/llm_pricing.py`. Tiw provisions `V2_STAGING_MISTRAL_API_KEY` in his password manager (not chat). The benchmark CLI `--benchmark-providers mock,mistral_ocr` then produces a side-by-side report. Estimate: $0.50–$2 per benchmark run; cap remains $5.
2. **Move to a different priority** per `docs/AI_COMMAND_CENTER.md` — customer memory continuity, human handoff polish, or admin dashboard v0.

**For Tiw:** No action required on this cleanup. The branch can sit at `e473a26` indefinitely; nothing in production is touched.

---

**Stopped.** Awaiting QA / Codex review per `AI_COMMAND_CENTER.md § "Handoff Rule"`.
