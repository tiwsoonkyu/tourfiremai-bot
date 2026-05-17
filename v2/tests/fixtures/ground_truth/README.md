# Ground Truth Fixtures for Sprint 4 PDF Corpus Accuracy

One JSON file per fixture PDF. Filename matches the PDF basename (without `.pdf`):
e.g. `WS01_japan_tokyo_5d4n.json` for `pdfs/text_based/WS01_japan_tokyo_5d4n.pdf`

## Schema

```json
{
  "pdf_filename": "WS01_japan_tokyo_5d4n.pdf",
  "pdf_hash": "<sha256>",
  "expected": {
    "tip_amount": 1500,
    "deposit_amount": 10000,
    "single_supplement": 5500,
    "visa_fee": null,
    "visa_status": "exempt",
    "infant_fee": 4500,
    "child_fee_no_bed": null,
    "joinland_price": null,
    "mandatory_fees_summary": "ทิป 1500 / มัดจำ 10000 / พักเดี่ยว 5500"
  },
  "expected_source_page": 4,
  "notes": "WS01 = anonymized partner; tour_code_real omitted"
}
```

## Required fields
- `tip_amount`, `deposit_amount`, `single_supplement` — must be int or `null`
- `visa_status` — one of `exempt`/`required`/`on_arrival`/`evisa`/`unknown` or `null`

## Optional fields
- `visa_fee`, `infant_fee`, `child_fee_no_bed`, `joinland_price`, `mandatory_fees_summary`
- `expected_source_page` (int) — page where the fee section actually appears

## Notes for hand-labelers
- Wholesale partner names → use `WS{N}` (WS01, WS02, ...) so cassettes/fixtures stay safe to commit
- Real fees from the PDF — write the actual amount, not what you'd like to see
- If a field is genuinely absent from PDF, set to `null` (NOT zero)
- For visa_status: "exempt" if "no visa required"/"ไม่ต้องวีซ่า", "required" if a specific fee or process is listed, "on_arrival"/"evisa" if explicitly stated
