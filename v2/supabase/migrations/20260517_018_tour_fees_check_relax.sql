-- Migration 018: relax tour_fees.extraction_method CHECK to include Sprint 3 R2 values
-- Old: ('pdfplumber','regex','ocr','llm_vision','manual')
-- New: ('pdfplumber+regex','regex','ocr','llm_text','llm_vision','manual','none')

ALTER TABLE tour_fees DROP CONSTRAINT IF EXISTS tour_fees_extraction_method_check;
ALTER TABLE tour_fees ADD CONSTRAINT tour_fees_extraction_method_check
  CHECK (extraction_method IN (
    'pdfplumber+regex', 'regex', 'ocr', 'llm_text', 'llm_vision', 'manual', 'none'
  ));
