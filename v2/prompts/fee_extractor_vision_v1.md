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
