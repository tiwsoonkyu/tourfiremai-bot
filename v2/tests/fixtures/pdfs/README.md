# PDF Fixtures for Sprint 3 Fee Extraction Tests

## Folder layout

- `text_based/` — PDFs where pdfplumber can read all text (most modern wholesale PDFs)
- `scanned/` — image-only PDFs (scans, JPEG-embedded) requiring vision LLM
- `mixed/` — partial text + scanned images on different pages

## Naming convention

`{wholesale_anonymized}_{country}_{program_short}.pdf`
e.g. `WS01_japan_tokyo_5d4n.pdf`

Use anonymized 2-char prefix `WS01..WS09` instead of partner brand name in filename to keep wholesale identity out of fixture corpus.

## Adding fixtures

3-5 real wholesale PDFs per folder. Recommended mix:
- text_based/: 2 fixtures (Japan, Vietnam)
- scanned/: 1 fixture (older Chinese partner)
- mixed/: 1 fixture

**Owner Tiw will provide real PDFs.** Until then, `test_fee_extractor_*` runs use:
- `tests/test_fee_extractor_regex.py` — Thai text strings (no actual PDF needed)
- `tests/test_fee_extractor_text.py` — pdfplumber on a synthetic PDF generated at runtime
- `tests/test_fee_extractor_vision.py` — mock vision client only
