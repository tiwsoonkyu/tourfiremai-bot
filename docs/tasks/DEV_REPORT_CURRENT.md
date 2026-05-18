# Dev Report — `DEV-2026-05-19-003`

**Status:** `READY_FOR_QA`
**Verdict recommendation to QA:** **`GO`**
**Author:** Claude Cowork Dev
**Date:** 2026-05-19
**Controller:** Codex
**Branch:** `v2/s4-followup-vision-ondemand` (pushed)
**HEAD commit:** `d68a4be` (parent: `ff28807` — Codex's task-resync)
**Spend this session:** **$0.00** — no live paid call, no live OpenAI call, no tests required credentials.

---

## 1. Status

`READY_FOR_QA`. The task asked for an *optional* paid OCR / document-parser provider abstraction + a benchmark harness, with NO live paid calls and NO weakening of the safety thresholds. Both pieces are in place; full unit suite goes from 469 → 495 passing / 0 failures.

---

## 2. Files Changed (committed on `v2/s4-followup-vision-ondemand` @ `d68a4be`)

```
v2/scraper/document_parser_provider.py    | 335 +++  (NEW — provider interface + mock + 3 fail-closed stubs)
v2/scraper/benchmark_providers.py         | 295 +++  (NEW — benchmark_providers() + format_benchmark_markdown)
v2/scraper/run_fee_pipeline.py            |  50 +    (modified — new --benchmark-providers flag)
v2/tests/test_document_parser_provider.py | 330 +++  (NEW — 26 tests across 7 classes)
4 files changed, 1010 insertions(+)
```

**Also written outside the repo (this report + status JSON exist in BOTH places):**
- `docs/tasks/DEV_REPORT_CURRENT.md` ← this file (in repo AND workspace folder).
- `docs/tasks/AGENT_STATUS.json` ← updated.

**NOT changed:** any V1 path, any prompt, any migration, any production env, any secret store, any Make.com module, any Meta webhook code, any deploy setting, `fee_answer_policy.py`, the orchestrator runtime path, `extract_fees_on_demand`, the existing on-demand cache. Verified by pre-commit grep and `git diff --name-only`.

---

## 3. Root Cause Analysis — why low money-critical accuracy on Phase 2

The first Phase 2 live recording on `1ec49e2` reported:
- tip 50% · deposit 70% · single_supplement 50% · hardest-required avg 56.7%

Investigation (from the PDF text, ground truth, and `extraction_accuracy._grade_numeric` scoring):

| Field | Failure mode |
|-------|--------------|
| `tip_amount` | WS01+WS02 ground truth = 2000 — regex catches WS01 (`ค่าทิป … 2,000 บาท`) but layout variations or vision hallucination on WS02/03/04/05 push the score to 50%. |
| `deposit_amount` | Similar to tip: regex catches the `บาท`-anchored cases on WS01; other PDFs ambiguous. |
| `single_supplement` | The hardest: real WS01 value `6,000` is the LAST COLUMN of a price-rate table (`ห้องพักเดี่ยว ท่าน 19 – 23 มิถุนายน 2569 19,990 19,990 15,990 6,000`). Neither regex (no `บาท` suffix on the value) nor general vision can be trusted to pick the correct column without table-aware parsing. |
| `visa_fee` | 100% — the rule-based visa_status='exempt' detection works for Japan/Korea/Taiwan PDFs. |

The earlier `d0a43bf` safeguards (vision-only cap 0.84, duplicate-value penalty 0.50) made the bot SAFER (more handoffs, fewer wrong answers) but did NOT change the source signal. To actually LIFT the numbers, we need a higher-fidelity parser for the table-heavy and image-heavy pages.

That's the design intent behind today's task: prepare the runway for a **table-aware paid OCR layer** without committing to one specific vendor yet, and without weakening any safety surface today. The benchmark harness is the next decision tool: implement `MistralOCRParser.parse()` (or `GoogleDocumentAIParser.parse()`), re-run on the corpus, compare against regex+vision side-by-side, choose the winner.

---

## 4. Summary of Changes

### `v2/scraper/document_parser_provider.py` (NEW)
Defines:
- `DocumentParserProvider` Protocol — every provider exposes `name`, `is_available()` (returns `(ok, reason)` — never touches network), and `parse(pdf_path, asked_field=None)` (returns `DocumentParseResult`).
- `DocumentParseResult` dataclass — provider-agnostic structured output (raw_text, tables, per-field values + per-field confidences, visa_status, source_page, latency, cost estimate, error).
- `ProviderNotAvailableError` (raised by stubs when creds/SDK missing) and `ProviderNotImplementedError` (raised when stubs reach `parse` despite having creds — safety net for unit tests).
- `MockDocumentParser` — in-process, deterministic, no network. Returns canned data keyed by PDF basename so tests + the benchmark grader get predictable accuracy numbers. Confidences capped at 0.85 so mock alone cannot answer single_supplement (policy 0.90) — the anti-guess invariant is preserved by design.
- Three stub paid providers: `MistralOCRParser`, `GoogleDocumentAIParser`, `AWSTextractParser`. Each carries its required `V2_STAGING_*` env vars + SDK import name + activation hint. `is_available()` returns `(False, "missing_credentials:…")` or `(False, "missing_sdk:…")`; `parse()` raises `ProviderNotAvailableError` before any network attempt.
- `make_document_parser(name)` factory + `available_providers()` list — used by the benchmark CLI.

### `v2/scraper/benchmark_providers.py` (NEW)
`benchmark_providers(pdfs, providers, *, ground_truth_dir) → BenchmarkReport`:
- For each requested provider, calls `is_available()` first. Unavailable providers are **skipped with a clear `skip_reason`** — no `parse()` invoked, no network reached.
- For available providers, iterates the PDF list, calls `parse()`, converts the result to an `ExtractionResult`, hands it to the existing `extraction_accuracy.grade_extraction()`, records per-PDF and aggregate scores (overall, hardest-required, per-field, source-page match, latency, estimated cost in USD with `unpriced_calls` tracking).
- `ProviderNotImplementedError` (stub providers) is caught **per-row** so a benchmark run with `mock,mistral_ocr` still produces the mock side.
- `format_benchmark_markdown(report)` renders the comparison table.

### `v2/scraper/run_fee_pipeline.py` (modified)
- New CLI flag `--benchmark-providers PROVIDER_NAMES` (comma-separated).
- Dispatch route: when set, calls `_run_benchmark_providers(args)`, which fans out across `v2/tests/fixtures/pdfs/{text_based,scanned,mixed}/*.pdf` against `v2/tests/fixtures/ground_truth/`, optionally writes the markdown report to `--output-report`.
- Default invocation `--benchmark-providers mock` runs entirely without credentials. Mixing in paid providers (`--benchmark-providers mock,mistral_ocr,google_document_ai,aws_textract`) is safe — paid providers are skipped at the `is_available()` gate.

### `v2/tests/test_document_parser_provider.py` (NEW — 26 tests across 7 classes)
| Class | Cases | Required category |
|-------|------:|-------------------|
| `TestProviderInterfaceContract` | 4 | 1 — interface contract |
| `TestMockProvider` | 4 | 2 — mock output |
| `TestPaidStubsFailClosed` | 8 (3 × 2 parametrized + 2) | 3 — missing creds fail closed; parse raises before network; SDK-only path; defensive "all creds, no SDK" |
| `TestBenchmarkRunnerMockOnly` | 3 | 4 — mock-only run; paid skipped no-network; markdown formatter renders skipped rows |
| `TestFeePolicyUnchanged` | 3 | 5 — thresholds unchanged; mock conf 0.85 cannot answer single_supplement (still handsoff) |
| `TestNoWholesaleLeakage` | 2 | 6 — grep blacklist regex on both production new files (scoped to runtime, not the test that contains the regex itself) |
| `TestPricingUnchanged` | 2 | 7 — d0a43bf 1000× fix still in place; format_cost_with_disclaimer surfaces "estimate" word |

All 7 task-spec test categories addressed.

---

## 5. Tests Run

```
PYTHONPATH=. pytest v2/tests/test_document_parser_provider.py -x
# 26 passed in 0.16s

PYTHONPATH=. pytest v2/tests --ignore=v2/tests/test_integration_staging.py \
                              --ignore=v2/tests/test_live_openai_health.py -q
# 495 passed in 2.81s — 0 failures, 0 regressions

# Safety scans on staged diff:
git diff | grep '^+' | grep -E 'sk-...|ghp_...|EAA...'              # 0 hits
git diff --name-only | grep V1-paths                                  # 0 hits
brand-leak regex on document_parser_provider.py + benchmark_providers.py  # CLEAN
```

The `test_integration_staging.py` (real staging DB) and `test_live_openai_health.py` (real OpenAI key) suites remain correctly ignored — neither is needed for this task, and the task's hard rules forbid live paid-provider calls.

---

## 6. Risks / Assumptions

### Assumptions
1. Codex's intent is to keep paid OCR **opt-in**, not the default. This commit reflects that: `extract_fees_on_demand` is unchanged; the orchestrator does NOT call any paid provider; the runtime fee-answer behavior is byte-identical to `d0a43bf`.
2. The `mock` provider's canned outputs are designed to score reasonably well against the existing ground-truth fixtures for benchmark plumbing tests, not to claim accuracy on real PDFs.
3. The pricing table in `v2/lib/llm_pricing.py` is OpenAI-only. When a real paid OCR provider is wired in, a new pricing dimension (per-page) will be added. Out of scope here.

### Carried-forward risks (not new)
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Phase 2 real-corpus accuracy on `d68a4be` is still unmeasured | Medium | Medium | This is operational, not code-side. Tiw still has to re-run on his dev machine if/when wanted. The provider abstraction is here so the *next* live attempt has more levers to pull. |
| A stub provider's `is_available()` could miss an SDK that's installed but renamed | Low | Low | `_sdk_import_name` is per-provider and easy to update when a real implementation lands. |
| Adding a paid OCR call later may interact with the on-demand cache (which is keyed by `pdf_hash + extraction_version`) | Low | Medium | When wiring is done, bump `EXTRACTION_VERSION` so the cache invalidates correctly. The current version constant is in `ondemand_vision.EXTRACTION_VERSION="1.0"`. |

### Hard rules — all respected
- ✅ V1 untouched.
- ✅ Make.com untouched.
- ✅ No deploy.
- ✅ No production webhook touched.
- ✅ No secret printed, written, or committed (pre-commit grep clean; PAT only in transient push URL; `.git/config` token-free after push).
- ✅ No live paid-provider call (stubs raise `ProviderNotAvailableError` before any network attempt; explicit unit tests verify this).
- ✅ No live OpenAI in unit tests (mock-mode default; spied paid-stub paths).
- ✅ No wholesale partner names introduced.
- ✅ Fee thresholds in `fee_answer_policy.py` UNCHANGED.
- ✅ Anti-guess invariant preserved (`TestFeePolicyUnchanged` proves it: mock conf 0.85 < policy 0.90 ⇒ handoff).
- ✅ No claim of exact OpenAI billing (estimator language only; `format_cost_with_disclaimer` surfaces "estimate, not exact OpenAI billing").

---

## 7. What QA Should Verify

| # | QA check (from `CURRENT_QA_TASK.md`) | How to verify |
|---|--------------------------------------|---------------|
| 1 | Dev stayed on V2 scope only | `git diff ff28807..d68a4be --name-only` → only `v2/` files |
| 2 | V1 production code unchanged | Same diff list contains no V1 paths (`app.py`, `fee_extractor.py`, `scraper.py`, `tourfiremai_dashboard.html`, etc.) |
| 3 | No Make.com / Cloudflare / Meta production webhook touched | Same diff; no `make_blueprint*`, no `cloudflare*`, no Meta webhook files |
| 4 | No secrets written to files | `git diff ff28807..d68a4be \| grep -E 'sk-[A-Za-z0-9_-]{20,}\|ghp_[A-Za-z0-9]{20,}\|EAA[A-Za-z0-9]{20,}'` → 0 hits |
| 5 | No live paid-provider call required by tests | `TestPaidStubsFailClosed` (8 cases) explicitly patches env to ensure stubs refuse to execute |
| 6 | No live OpenAI required by unit tests | Suite runs without `V2_STAGING_OPENAI_API_KEY`; `test_integration_staging` + `test_live_openai_health` ignored |
| 7 | Provider abstraction fails closed when creds/provider missing | `TestPaidStubsFailClosed::test_no_creds_is_not_available` and `…test_parse_without_creds_raises_provider_not_available` (parametrized over all 3 paid stubs) |
| 8 | Benchmark path runs with a mock provider | `TestBenchmarkRunnerMockOnly::test_default_mock_only_runs_without_credentials` |
| 9 | Fee thresholds were not weakened | `TestFeePolicyUnchanged::test_thresholds_unchanged` asserts `DEFAULT_THRESHOLD == 0.80` and `SINGLE_SUPPLEMENT_THRESHOLD == 0.90` |
| 10 | Fee policy still handsoff below threshold | `TestFeePolicyUnchanged::test_low_confidence_single_supplement_still_handsoff` + the d0a43bf wire-in anti-guess test (still passing) |
| 11 | No wholesale partner names in new prompts/reports/cassettes | `TestNoWholesaleLeakage::test_no_brand_leak` parametrized over `document_parser_provider.py` and `benchmark_providers.py` (test file scoped out — it contains the blacklist regex by necessity) |
| 12 | Dev report clearly explains accuracy/cost tradeoff and next step | §3 (RCA), §8 (next step) |

### How QA should run the suite

```bash
git fetch origin v2/s4-followup-vision-ondemand
git checkout d68a4be
source .venv/bin/activate
pip install -r v2/requirements-dev.txt reportlab pdf2image Pillow
PYTHONPATH=. pytest v2/tests --ignore=v2/tests/test_integration_staging.py \
                              --ignore=v2/tests/test_live_openai_health.py
```

Expected: **495 passed**.

---

## 8. Next Recommended Step

**For Codex (Controller):**

1. Accept this `GO` verdict (or `GO_WITH_NOTES` if QA wants to flag that real-corpus accuracy lift is still unmeasured — operational, not Dev-correctable).
2. Decide whether to implement a paid OCR provider next. Suggested order (cheapest-to-experiment first):
   - **Mistral OCR** (low latency, strong Thai support). Task scope:
     a. Get a `V2_STAGING_MISTRAL_API_KEY` from Mistral.
     b. `pip install mistralai`.
     c. Implement `MistralOCRParser.parse()` using `mistralai.Mistral(api_key=…).ocr.process(document_url=…)` — uploads the PDF or passes a signed URL; parses the structured response into `DocumentParseResult`.
     d. Add Mistral OCR pricing to `v2/lib/llm_pricing.py` (per-page basis; check Mistral's quote).
     e. Re-run `--benchmark-providers mock,mistral_ocr --output-report docs/SPRINT_4_BENCHMARK_REPORT.md` on the 5-PDF corpus.
   - If Mistral underperforms, try Google Document AI form parser (excellent for tables) or AWS Textract.
3. After choosing a winner, write a small wiring task that adds the call site in `extract_fees_on_demand` (e.g. when regex+vision returns low-confidence on a money-critical field for a locked tour). Cache key still `pdf_hash + extraction_version`; bump `EXTRACTION_VERSION` to invalidate.
4. Re-measure on the real 5-PDF corpus and close Sprint 4 against `SPRINT_4_PLAN.md § 5` gates.

**For Tiw:**

- No action required for this commit (pure infrastructure).
- When Codex assigns the Mistral OCR wiring task, Tiw provisions the API key in his password manager (NOT chat) and runs the benchmark.

---

**Stopped.** Awaiting QA / Codex review per `AI_COMMAND_CENTER.md § "Handoff Rule"`.
