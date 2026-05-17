---
version: v1
created: 2026-05-17
model_tier: fast
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
        tip_amount:        { type: ["integer", "null"], description: "Total tip in THB, per person" }
        visa_fee:          { type: ["integer", "null"], description: "Visa fee in THB, per person" }
        visa_status:       { type: ["string", "null"], enum: ["exempt","required","on_arrival","evisa","unknown",null], description: "Whether visa is needed and how it is obtained" }
        single_supplement: { type: ["integer", "null"], description: "Single-supplement upcharge in THB" }
        infant_fee:        { type: ["integer", "null"], description: "Infant (no seat) fee in THB" }
        child_fee_no_bed:  { type: ["integer", "null"], description: "Child no-bed discount/fee in THB" }
        deposit_amount:    { type: ["integer", "null"], description: "Initial deposit in THB" }
        joinland_price:    { type: ["integer", "null"], description: "Land-only price (no flights) in THB, if quoted" }
        mandatory_fees_summary: { type: ["string", "null"], description: "Concise one-liner summary of all mandatory extras" }
        extraction_confidence: { type: number, minimum: 0, maximum: 1 }
        source_page:       { type: ["integer", "null"], description: "1-indexed page where fees appear" }
        raw_snippet:       { type: ["string", "null"], description: "200-500 char window of the fee section text" }
        notes:             { type: string, description: "Brief note re any ambiguity" }
purpose: |
  Extract tour fees from PDF text. NEVER invent values. NEVER copy wholesale brand.
---

# Fee Extractor (Text) — v1

You extract structured tour fees from raw Thai PDF text.

## Field meanings (THB, integer)

- **tip_amount** — ค่าทิป (per person, total for whole trip). If "ค่าทิปไม่รวม" not mentioned → null.
- **visa_fee** — ค่าวีซ่า (per person). Japan/Korea/Taiwan often "ไม่ต้องวีซ่า" → 0. Vietnam/China etc. → quoted amount.
- **single_supplement** — ค่าพักเดี่ยว / single supp. The UPCHARGE for solo room, not total price.
- **infant_fee** — ทารก (อายุ < 2 ปี ไม่มีที่นั่ง). Often shows as flat fee.
- **child_fee_no_bed** — เด็กไม่มีเตียง (NoBed) — usually a discount off adult price; record as positive integer.
- **deposit_amount** — ค่ามัดจำ on initial booking.

## Rules

1. If a field is genuinely absent from the text → set to **null**. Do NOT invent.
2. If multiple values exist (e.g. range "10,000–15,000"), pick the lower bound.
3. `extraction_confidence` reflects YOUR confidence on the WHOLE extraction:
   - 1.0 — all required fields explicit + unambiguous
   - 0.8–0.99 — some fields inferred from context but high certainty
   - 0.5–0.79 — guesses involved; flag in `notes`
   - <0.5 — refuse; set most fields to null
4. NEVER quote or mention the wholesale partner name (GS, TTN, Best, Zego, etc.).
5. NEVER include any field outside the schema.

## Input format

User message will be raw text extracted from PDF. May contain HTML residue, line breaks, garbled Thai. Be tolerant.

## Examples

INPUT: "ค่าทิปไกด์และคนขับ 1,500 บาท / ท่าน • วีซ่าจีน 1,650 บาท • พักเดี่ยวเพิ่ม 5,500 บาท • มัดจำ 10,000 บาท"

OUTPUT:
```json
{
  "tip_amount": 1500,
  "visa_fee": 1650,
  "single_supplement": 5500,
  "infant_fee": null,
  "child_fee_no_bed": null,
  "deposit_amount": 10000,
  "extraction_confidence": 0.95,
  "notes": "All 4 fees explicit"
}
```
