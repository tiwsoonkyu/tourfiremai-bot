---
version: v1
created: 2026-05-17
model_tier: vision
temperature: 0.0
max_tokens: 600
response_format:
  type: json_schema
  json_schema:
    name: TourFees
    strict: true
    schema:
      type: object
      additionalProperties: false
      required:
        - tip_amount
        - visa_fee
        - visa_status
        - single_supplement
        - infant_fee
        - child_fee_no_bed
        - deposit_amount
        - joinland_price
        - mandatory_fees_summary
        - extraction_confidence
        - source_page
        - raw_snippet
        - notes
      properties:
        tip_amount:        { type: ["integer", "null"] }
        visa_fee:          { type: ["integer", "null"] }
        visa_status:       { type: ["string", "null"], enum: ["exempt","required","on_arrival","evisa","unknown",null] }
        single_supplement: { type: ["integer", "null"] }
        infant_fee:        { type: ["integer", "null"] }
        child_fee_no_bed:  { type: ["integer", "null"] }
        deposit_amount:    { type: ["integer", "null"] }
        joinland_price:    { type: ["integer", "null"] }
        mandatory_fees_summary: { type: ["string", "null"] }
        extraction_confidence: { type: number, minimum: 0, maximum: 1 }
        source_page:       { type: ["integer", "null"] }
        raw_snippet:       { type: ["string", "null"] }
        notes:             { type: string }
purpose: |
  Vision fallback when PDF text extraction fails (scanned/image-based PDFs).
  Same field semantics as text extractor.
---

# Fee Extractor (Vision) — v1

You receive a rendered PDF page image (one or more) of a Thai tour program brochure.

Same rules as `fee_extractor_text_v1.md` apply. Same JSON schema.

Additional vision-specific guidance:
- Look for sections labeled: "ค่าใช้จ่ายเพิ่มเติม", "ราคาไม่รวม", "อัตราค่าบริการ", "หมายเหตุ"
- Tables with numeric columns are usually fee schedules
- If image is too blurry to read confidently → set `extraction_confidence < 0.5` and set most fields to null
- NEVER quote wholesale partner names visible in headers/footers/watermarks

End of vision prompt.


## Phase 2 follow-up rules (anti-hallucination — money-critical)

The four money-critical fields (`tip_amount`, `deposit_amount`,
`single_supplement`, `visa_fee`) need EXTRA care:

A. **Require a clear fee context** to fill these. A value qualifies as
   "clear fee context" ONLY when it satisfies BOTH:
     1. The value is adjacent (≤ 80 chars on the same logical line, or on
        the next line with no intervening table-cell separator) to one of:
        `ค่าทิป`, `ทิปไกด์`, `ทิป...บาท`, `tip ... baht`,
        `มัดจำ`, `ชำระเงินมัดจำ`, `deposit ... baht`,
        `พักเดี่ยวเพิ่ม`, `พักเดี่ยว ... บาท`, `single supplement ... baht`,
        `ค่าวีซ่า`, `visa ... baht`.
     2. The value is followed within ~6 characters by `บาท` or `baht`
        (Thai/EN currency suffix).

B. **NEVER copy a numeric value out of a price-rate table** (a table where
   rows are dates and columns are prices for different occupancy types) into
   any money-critical field, even if a column header has the keyword
   `พักเดี่ยว` or `Single`. Those columns are *occupancy upgrades*, not
   the per-trip fee fields. If the only numeric clue you see for a field is
   inside such a table, leave that field NULL.

C. **NEVER reuse the same number across two different fields.** If you've
   already set `deposit_amount = 15,000`, do not also set
   `single_supplement = 15,000` unless the text explicitly states both
   values are 15,000 with their own "บาท" suffix.

D. If you're not certain (genuine ambiguity in the text), set the field to
   `null` and reduce `extraction_confidence` by 0.1 per uncertain field.
   Then explain in `notes` which field was uncertain and why.

E. `visa_status = "exempt"` is the right answer for Japan/Korea/Taiwan
   programs when there is no `visa_fee` mentioned — even if you see no
   explicit "ไม่ต้องวีซ่า" string. Use the destination country as the prior.


## Phase 2 live-accuracy follow-up — Worked examples (Sprint 4)

The Phase 2 live run on the 5-PDF corpus showed false positives where a
page mentioned a fee keyword in passing but the actual fee wasn't on that
page. The rules below disambiguate those cases.

### Wrong vs Right

**WRONG (do NOT do this):**

Page text fragment:
> "อัตราค่าบริการ ห้องพักเดี่ยว ท่าน 19 – 23 มิถุนายน 2569 19,990 19,990 15,990 6,000"

WRONG extraction (table-column hallucination):
```json
{"single_supplement": 19990, "tip_amount": 19990}
```
Reason this is WRONG:
- The number 19,990 is a *date-range price column*, NOT a single-supplement
  upcharge. No "บาท" suffix appears next to it; the row is a price-rate
  table, not a fee statement.
- Tip is not mentioned ANYWHERE on this fragment.

RIGHT extraction:
```json
{"single_supplement": null, "tip_amount": null}
```
Or, if you can confidently read the LAST column (the labeled "พักเดี่ยว"
column) AND it's clearly a per-trip upcharge in THB:
```json
{"single_supplement": 6000}
```
…but ONLY if you are certain the column label is "พักเดี่ยว" and the column
contains exactly that fee. If the column structure is ambiguous, set null.

### Duplicate-number rule

If you find yourself writing the SAME numeric value into two different
money-critical fields (e.g. tip_amount = single_supplement = 5500), STOP.
Real wholesale tour fees almost never coincide between two independent
line items (tip, deposit, single-supplement, visa). A duplicate is almost
always a hallucination from the same context. Set BOTH fields to null and
record the reason in `notes`.

### "Not in the text" rule

For each money-critical field, ask yourself: did you SEE this fee
explicitly stated with both a fee keyword (ค่าทิป / ทิปไกด์ / มัดจำ /
พักเดี่ยวเพิ่ม / ค่าวีซ่า) AND a "บาท" or "baht" suffix on the same line
(or contiguous lines)? If NO → the field is null. If yes → extract the
adjacent numeric value.

### Confidence calibration after these rules

- All required fields present with strong fee context + "บาท" → `extraction_confidence` ≥ 0.90
- Some fields explicit, some inferred from layout → 0.70–0.89
- One or more fields you set to null due to genuine absence → 0.85+ on
  the ones you DID populate (your null preference is correct, not a
  weakness)
- If you set values to null due to the duplicate-number rule above, drop
  `extraction_confidence` by 0.1 and explain in `notes`.
