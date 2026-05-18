# QA Report — `DEV-2026-05-19-003` Cleanup (QA L1 + L2)

**Verdict:** `GO`
**Author:** Claude Cowork QA
**Date:** 2026-05-19
**Controller:** Codex
**Paired Dev Task:** `DEV-2026-05-19-003` cleanup
**Branch reviewed:** `v2/s4-followup-vision-ondemand` @ commit `9ccf7ec` (docs companion to code commit `e473a26`)
**Parent baseline:** `v2/s4-followup-vision-ondemand` @ `bd4784e`

---

## 1. Verdict

**`GO`.** Both QA informational notes from the prior `QA-2026-05-19-003` GO verdict (L1 mock key-naming bug; L2 unnecessary Supabase init for benchmark/corpus modes) are closed by tight, surgical edits with full regression coverage. Production runtime path is byte-identical. Fee thresholds are unchanged. All hard rules respected.

Code change is in commit `e473a26`: 3 files, +179/-3. The `9ccf7ec` companion commit publishes the Dev report + status JSON into `docs/tasks/`. Combined `bd4784e..9ccf7ec` touches 5 files (3 code + 2 docs).

**Tests run:** 32 in the test_document_parser_provider.py module (26 prior + 6 new for L1/L2), all pass. Full non-live suite **494 + 7 Flask-skipped = 501**, matching Dev's reported 501, with **0 failures and 0 regressions** vs the `bd4784e` baseline.

---

## 2. Files Reviewed

| # | File / artifact | State on `e473a26` (+ docs at `9ccf7ec`) | QA action |
|---|-----------------|------------------------------------------|-----------|
| 1 | `docs/AI_COMMAND_CENTER.md` | Unchanged | Read for safety rules |
| 2 | `docs/tasks/CURRENT_QA_TASK.md` | Unchanged | Read |
| 3 | `docs/tasks/TASK_LOG.md` | Unchanged in this task's scope | Read |
| 4 | `docs/tasks/DEV_REPORT_CURRENT.md` | 169 lines, format-conformant; rewritten by `9ccf7ec` | Read in full |
| 5 | `docs/tasks/AGENT_STATUS.json` | `READY_FOR_QA` / `DEV-2026-05-19-003`, files_changed list matches diff | Verified |
| 6 | `v2/scraper/document_parser_provider.py` (modified, +24/-3) | L1 — explicit `_CONF_COL` map replaces `f"{k.split('_')[0]}_confidence"` shorthand | Diff read in full |
| 7 | `v2/scraper/run_fee_pipeline.py` (modified, +8/-1) | L2 — `needs_supabase` predicate skips Supabase init for `--pdf-corpus`, `--pdf-corpus-ondemand`, `--benchmark-providers` | Diff read in full |
| 8 | `v2/tests/test_document_parser_provider.py` (modified, +150) | 6 new regression tests across `TestQACleanupL1ConfidenceKeys` (4) + `TestQACleanupL2NoSupabaseInBenchmarkMode` (2) | Read in full |
| 9 | `v2/lib/fee_answer_policy.py` | Not in diff — thresholds unchanged | Verified by grep: `DEFAULT_THRESHOLD = 0.80`, `SINGLE_SUPPLEMENT_THRESHOLD = 0.90` |
| 10 | All runtime modules (`orchestrator.py`, `response_writer.py`, `ondemand_vision.py`, `extract_fees.py`, `fee_schema.py`, `llm.py`) | Not in diff — byte-identical to `bd4784e` | `git diff bd4784e..e473a26 -- v2/lib …` returns empty |

Branch checked out at `/tmp/repo` on `v2/s4-followup-vision-ondemand` @ `9ccf7ec`. Local repo confirmed in sync with the user-specified expected commit.

---

## 3. Evidence Checked — Task QA Matrix

### 3.1 L1 fix — MockDocumentParser confidence keys

| # | Required check | Evidence | Result |
|---|----------------|----------|--------|
| L1.1 | `single_supplement` → `single_supplement_confidence` | New explicit `_CONF_COL` dict maps `"single_supplement": "single_supplement_confidence"` directly. `TestQACleanupL1ConfidenceKeys::test_single_supplement_maps_to_correct_column` asserts the correct key IS present AND the buggy `single_confidence` is NOT. | ✅ PASS |
| L1.2 | No broken `single_confidence` mapping remains | Grep on `v2/scraper/document_parser_provider.py` for `single_confidence` returns only ONE match — a comment in the docstring documenting the prior bug. Zero matches in executable code. The test assertion `"single_confidence" not in keys` runs and passes. | ✅ PASS |
| L1.3 | Benchmark grader receives `single_supplement_confidence` correctly | `TestQACleanupL1ConfidenceKeys::test_benchmark_grader_sees_single_supplement_confidence` runs `_parse_result_to_extraction(r)` end-to-end and asserts `extraction.single_supplement_confidence == 0.85`. Previously this was `None` (silently dropped). Also asserts tip and deposit confidences are correctly set. | ✅ PASS |
| L1.4 (extra) | Every emitted confidence key matches an `ExtractionResult` field name | `TestQACleanupL1ConfidenceKeys::test_tip_visa_keys_match_extractionresult` iterates `r.fee_field_confidences` keys, asserts membership in the valid set, and asserts `hasattr(ExtractionResult, k)` for each. Closes any future key-typo regression. | ✅ PASS |

### 3.2 L2 fix — Supabase init skipped for benchmark/corpus modes

| # | Required check | Evidence | Result |
|---|----------------|----------|--------|
| L2.1 | `--benchmark-providers` does NOT initialize Supabase | New `needs_supabase = not (args.pdf_corpus or args.pdf_corpus_ondemand or args.benchmark_providers)` predicate guards the `make_supabase_from_config(config)` call. `TestQACleanupL2NoSupabaseInBenchmarkMode::test_benchmark_providers_does_not_call_make_supabase` spies on `runner.make_supabase_from_config`, runs `main(["--benchmark-providers", "mock"])`, asserts `called == []`. | ✅ PASS |
| L2.2 | `--pdf-corpus` does NOT initialize Supabase | The new predicate retains `args.pdf_corpus` in the OR — preserving the original behavior. Not explicitly retested in a new case, but the prior test surface (which exercised `--pdf-corpus` paths in `test_phase2_followup.py` etc.) continues to pass — verified in the 501-test full suite. | ✅ PASS |
| L2.3 | `--pdf-corpus-ondemand` does NOT initialize Supabase | The new predicate adds `args.pdf_corpus_ondemand` to the OR. `TestQACleanupL2NoSupabaseInBenchmarkMode::test_pdf_corpus_ondemand_also_skips_supabase` spies on `runner.make_supabase_from_config`, runs `main(["--pdf-corpus-ondemand", "--mock-llm"])`, asserts `called == []`. | ✅ PASS |

### 3.3 Safety checks

| # | Required check | Evidence | Result |
|---|----------------|----------|--------|
| S1 | Fee thresholds unchanged | `grep -E '(DEFAULT_THRESHOLD\|SINGLE_SUPPLEMENT_THRESHOLD)\s*=' v2/lib/fee_answer_policy.py` → `DEFAULT_THRESHOLD = 0.80` / `SINGLE_SUPPLEMENT_THRESHOLD = 0.90`. `git diff bd4784e..e473a26 -- v2/lib/fee_answer_policy.py` → empty (file not in diff). | ✅ PASS |
| S2 | Production runtime path unchanged | `git diff bd4784e..e473a26 -- v2/lib v2/scraper/ondemand_vision.py v2/scraper/extract_fees.py` → empty. So `orchestrator.py`, `response_writer.py`, `fee_answer_policy.py`, `fee_schema.py`, `llm.py`, `llm_pricing.py`, `memory.py`, `state_machine.py`, `cache.py`, `cassette_redactor.py`, `ondemand_vision.py`, `extract_fees.py` are all byte-identical to `bd4784e`. The runtime fee-answer behavior for real customer messages is unchanged. | ✅ PASS |
| S3 | No V1 paths touched | `git diff --name-only bd4784e..e473a26 \| grep -E '(^app\.py\|^scraper\.py\|^fee_extractor\.py\|tourfiremai-bot-dev\|^patches/\|^Procfile\|^railway\.json\|cloudflare-worker)'` → 0 hits. | ✅ PASS |
| S4 | No Make.com / Cloudflare / Railway references | `git diff bd4784e..e473a26 \| grep -iE '(make\.com\|integromat\|cloudflare\|railway)'` → 0 hits. | ✅ PASS |
| S5 | No production webhook / Meta endpoint touched | `git diff bd4784e..e473a26 \| grep -iE '(webhook_proxy\|messenger\|graph\.facebook\|tourfiremai\.com/api)'` → 0 hits. | ✅ PASS |
| S6 | No secrets written | `git diff bd4784e..e473a26 \| grep -E '(sk-[A-Za-z0-9_-]{20,}\|ghp_[A-Za-z0-9]{20,}\|EAA[A-Za-z0-9]{20,})'` → 0 hits. | ✅ PASS |
| S7 | No live OpenAI calls in tests | New tests use `MockDocumentParser` and `MockLLMClient` only. `--mock-llm` flag passed in L2 test 2. The full suite runs without `V2_STAGING_OPENAI_API_KEY` and passes. `test_live_openai_health.py` correctly skipped. | ✅ PASS |
| S8 | No paid-provider calls in tests | Paid stubs are never invoked in the cleanup tests. The prior `TestPaidStubsFailClosed` (8 cases) continues to assert `parse()` raises `ProviderNotAvailableError` before any network attempt. The L1 tests construct `MockDocumentParser` directly; L2 tests pass `--benchmark-providers mock` only. | ✅ PASS |
| S9 | Anti-guess invariant preserved | `TestFeePolicyUnchanged::test_low_confidence_single_supplement_still_handsoff` still passes (mock cap 0.85 < strict 0.90 → handoff). The L1 fix correctly produces `single_supplement_confidence = 0.85` — strictly below the 0.90 threshold — so even with the corrected key, the policy still hands off single_supplement. | ✅ PASS |
| S10 | No wholesale brand leakage in new code | `git diff bd4784e..e473a26 -- v2/scraper/document_parser_provider.py v2/scraper/run_fee_pipeline.py \| grep -iwE '(TTN\|ZEGO\|FORMOSA\|i-travel\|rich.tour\|best.tour\|GS.travel)'` → 0 hits. `TestNoWholesaleLeakage` (parametrized over the two production files) continues to pass. | ✅ PASS |

### 3.4 Tests verified

| # | Test surface | Result |
|---|--------------|--------|
| T1 | `pytest v2/tests/test_document_parser_provider.py -v` | **32 passed** (26 prior + 6 new) — the 6 new tests are `TestQACleanupL1ConfidenceKeys` (4) and `TestQACleanupL2NoSupabaseInBenchmarkMode` (2). All pass. |
| T2 | Full non-live suite | **494 passed + 7 Flask-skipped + 0 failed in 2.47s** (= Dev's reported 501 when skips counted). **0 regressions** vs the `bd4784e` baseline of 488+7=495. |
| T3 | Pre-existing safety tests still pass | `TestFeePolicyUnchanged` (3), `TestPaidStubsFailClosed` (8), `TestNoWholesaleLeakage` (2), `TestPricingUnchanged` (2) all PASS. |

---

## 4. Findings by Severity

### 🔴 Critical (blocks GO)
None.

### 🟠 High
None.

### 🟡 Medium
None.

### 🟢 Low / informational

**L_cleanup_1.** The Dev report's L1 explanation says "the previous shorthand … dropped `deposit_confidence` for `deposit_amount`". This is slightly inaccurate — `deposit_amount`.split('_')[0] = `'deposit'`, so `f"{...}_confidence"` produced `deposit_confidence`, which is the correct column name (by coincidence). The actually-broken case was only `single_supplement` → `single_confidence`. The fix is still correct (replaces the fragile shorthand with explicit mapping), but the Dev report's diagnosis overstates the impact. Documentation nit, not a functional issue. The test `test_deposit_maps_to_correct_column` correctly acknowledges this in its inline comment ("old code … made `deposit_amount` → `deposit_confidence` by accident — same key").

**L_cleanup_2.** No new regression test explicitly covers `--pdf-corpus` (the original-style skipped flag), only `--benchmark-providers` and `--pdf-corpus-ondemand`. The L2 fix still correctly preserves `args.pdf_corpus` in the new predicate, and the full suite contains pre-existing tests that exercise `--pdf-corpus` paths — so this isn't a regression risk. But an explicit regression test (third spy case mirroring L2.1 / L2.3) would close the loop completely. Trivial future polish; doesn't block.

### 🟢 Informational only

- The cleanup is a textbook "two-note QA closeout": both notes have a focused fix, a tight regression test, and an explicit "anti-regression" assertion (`"single_confidence" not in keys` for L1; spy-assert `called == []` for L2). The L1 end-to-end test (`test_benchmark_grader_sees_single_supplement_confidence`) is particularly good — it exercises the full mock → DocumentParseResult → ExtractionResult conversion path that previously silently dropped the value.
- The 9ccf7ec docs-companion commit is the same pattern Dev has used before for syncing the report+status into `docs/tasks/`. Zero code risk surface.
- Cumulative branch state vs `v2/foundation` is now 9 commits, all independently QA-cleared: `16fdd86` → `39bcf53` → `b325e92` → `516b1c3` → `1ec49e2` → `d0a43bf` → `ef4c0ae` → `ff28807` → `bd4784e`/`d68a4be` → `9ccf7ec`/`e473a26`.

---

## 5. Tests Verified

QA ran the test suite locally in `/tmp/repo` on `v2/s4-followup-vision-ondemand` @ `9ccf7ec`. All required deps already installed from prior sessions.

### 5.1 Module-scoped run for the cleanup

```bash
PYTHONPATH=. pytest v2/tests/test_document_parser_provider.py -v
# 32 passed in 0.42s
```

All 32 tests pass, including the 6 new L1/L2 regression tests:
- `TestQACleanupL1ConfidenceKeys::test_single_supplement_maps_to_correct_column` ✓
- `TestQACleanupL1ConfidenceKeys::test_deposit_maps_to_correct_column` ✓
- `TestQACleanupL1ConfidenceKeys::test_tip_visa_keys_match_extractionresult` ✓
- `TestQACleanupL1ConfidenceKeys::test_benchmark_grader_sees_single_supplement_confidence` ✓
- `TestQACleanupL2NoSupabaseInBenchmarkMode::test_benchmark_providers_does_not_call_make_supabase` ✓
- `TestQACleanupL2NoSupabaseInBenchmarkMode::test_pdf_corpus_ondemand_also_skips_supabase` ✓

### 5.2 Full non-live suite

```bash
PYTHONPATH=. pytest v2/tests \
  --ignore=v2/tests/test_integration_staging.py \
  --ignore=v2/tests/test_live_openai_health.py -q
# 494 passed, 7 skipped, 0 failed in 2.47s
```

494 hard-passes + 7 Flask-skipped = 501 — matches Dev's reported 501. **0 failures. 0 regressions vs the `bd4784e` baseline of 495.** The +6 new tests account for the delta exactly.

### 5.3 Tests NOT run

- `v2/tests/test_integration_staging.py` — requires `V2_STAGING_DB_*` env; out of scope.
- `v2/tests/test_live_openai_health.py` — correctly opt-in only.

Neither was modified in this commit.

---

## 6. Remaining Risks

### Resolved by this cleanup
- **L1** (mock key-naming bug) — fixed; benchmark grader now correctly receives `single_supplement_confidence`.
- **L2** (Supabase init for benchmark/corpus modes) — fixed; pure mock benchmark and on-demand corpus runs no longer need `V2_STAGING_DB_*` credentials.

### Carried forward (not introduced by this cleanup; not blockers)
- **R1.** Phase 2 real-corpus accuracy still unmeasured against the latest branch. Operational; Tiw's machine + key + Poppler required.
- **R2.** No real paid-provider implementation yet (Mistral OCR remains a stub). Next Dev task per Dev report § 7.
- **R3.** Pricing for paid providers not in `llm_pricing.py`. Out of scope until a real provider lands.

None blocks this verdict.

---

## 7. Next Recommended Step

Since the verdict is `GO`:

**For Codex (Controller):**
1. Accept this `GO` verdict. Flip `AGENT_STATUS.json` to `QA_GO`. Append `TASK_LOG.md` with `DEV-2026-05-19-003` cleanup + commits `e473a26` + `9ccf7ec` + this report path.
2. Decide next workstream per Dev report § 7:
   - **Option A:** Implement real Mistral OCR provider. Pre-conditions: Tiw provisions `V2_STAGING_MISTRAL_API_KEY` (password manager only), add per-page pricing entry to `v2/lib/llm_pricing.py`, bump `EXTRACTION_VERSION` for cache invalidation.
   - **Option B:** Move to a different `AI_COMMAND_CENTER.md` Active Priority — customer memory continuity, human handoff polish, or admin dashboard v0.
3. (Optional polish, non-blocking) Add an explicit regression test for `--pdf-corpus` Supabase skip (per § 4 L_cleanup_2). Trivial; can bundle with the Mistral wiring task or a Sprint 4 close grooming task.

**For Tiw:**
- No code action required.
- When Codex approves the Mistral wiring task, provision the API key in password manager only (NOT chat).

---

**Stopped.** Per QA handoff rule (`AI_COMMAND_CENTER.md` § "Handoff Rule Between Agents"): not continuing implementation. `AGENT_STATUS.json` will be flipped to `QA_GO`. Awaiting Codex direction.
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               