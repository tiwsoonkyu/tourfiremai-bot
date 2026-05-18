# Current Dev Task

Task ID: `DEV-2026-05-19-003`
Status: `PENDING`
Assigned role: Claude Cowork Dev
Controller: Codex

## Task

Implement the next safe layer for PDF fee accuracy: an optional paid Document/OCR provider abstraction and benchmark harness, without making live paid-provider calls.

## Context

TourFireMai V2 Sales Agent must answer money-critical fee questions accurately:

- guide tip
- deposit
- single supplement
- visa fee / visa status

Current Sprint 4 on-demand vision work improved the architecture but live Phase 2 recording still showed weak accuracy on the hardest fields:

- overall average accuracy: about 73.8%
- hardest-required average accuracy: about 56.7%
- tip_amount: about 50%
- single_supplement: about 50%
- deposit_amount: about 70%

The latest branch already includes follow-up work through commit `ef4c0ae`. This task supersedes the older `S4-LIVE-DEV-2026-05-18-001` live-run task, which is closed as blocked/superseded because Claude Cowork cannot access Tiw's local shell secrets and this task must not spend live provider tokens.

Tiw approved adding a paid helper if it improves accuracy, but paid OCR must be used only on-demand and only when needed for fee extraction. It must not become the default path for every PDF.

## Scope

You may modify V2 code, tests, and docs only.

Required work:

1. Investigate the low accuracy on tip_amount, deposit_amount, and single_supplement.
2. Verify the corrected internal cost-estimator unit semantics from the latest branch. Patch only if still wrong.
3. Add an optional document parser provider abstraction for future paid OCR tools.
4. Add a benchmark-ready interface so providers can be compared on the same PDF corpus.
5. Keep current deterministic / safety-first behavior:
   - answer only when confidence threshold is met
   - otherwise handoff
   - never lower thresholds just to increase answer rate

Suggested implementation shape:

- `v2/scraper/document_parser_provider.py`
  - provider interface
  - `mock` provider for tests
  - stubs for future providers such as:
    - `mistral_ocr`
    - `google_document_ai`
    - `aws_textract`
  - stubs must fail closed with clear missing-provider / missing-credentials errors
  - no live provider calls in unit tests

- benchmark hook / runner extension
  - compare current regex + vision against optional document parser output
  - record field-level accuracy, source page match, latency, estimated cost
  - do not require paid provider credentials to run the default test suite

If you find a smaller or cleaner design, implement that, but explain the tradeoff in the Dev report.

## Hard Constraints

- Do not touch V1 production behavior.
- Do not touch Make.com.
- Do not deploy anything.
- Do not change production webhook behavior.
- Do not print, write, or commit secrets.
- Do not make live paid-provider calls.
- Do not make live OpenAI calls in unit tests.
- Do not introduce wholesale partner names into customer-facing output, prompts, logs, cassettes, or reports.
- Do not weaken fee safety thresholds.
- Do not claim exact OpenAI billing. Use estimator language only.

## Required Tests

Add or update tests for:

1. Provider interface contract.
2. Mock provider output.
3. Missing credentials fail closed.
4. Benchmark runner can run without live paid provider credentials.
5. Fee policy still handoffs when confidence is below threshold.
6. No wholesale brand leakage in new prompts/reports/cassettes.
7. Pricing estimator tests if any pricing code changes.

Run the broad non-live V2 suite if feasible.

## Deliverables

Write:

`docs/tasks/DEV_REPORT_CURRENT.md`

Also update:

`docs/tasks/AGENT_STATUS.json`

Use status:

`READY_FOR_QA`

## Dev Report Requirements

Include:

1. Status
2. Files changed
3. Root cause analysis
4. Summary of changes
5. Tests run
6. Risks / assumptions
7. What QA should verify
8. Next recommended step

## Stop Condition

After writing the Dev report and AGENT_STATUS, stop and wait for QA/Codex review.
