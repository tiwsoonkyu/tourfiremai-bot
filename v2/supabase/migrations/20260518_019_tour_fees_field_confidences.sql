-- Migration 019: Add field-level confidence columns to tour_fees
-- Sprint 4 follow-up: on-demand vision/OCR extraction needs per-field confidence
-- so the bot can answer fee questions with field-level precision instead of
-- relying on row-level extraction_confidence only.
--
-- Additive ALTERs only — safe to re-run. NULL allowed for backward compat with
-- rows extracted before this migration (response_writer falls back to
-- extraction_confidence when per-field column is NULL).

ALTER TABLE tour_fees
  ADD COLUMN IF NOT EXISTS tip_confidence               DOUBLE PRECISION
    CHECK (tip_confidence               IS NULL OR (tip_confidence               BETWEEN 0 AND 1)),
  ADD COLUMN IF NOT EXISTS deposit_confidence           DOUBLE PRECISION
    CHECK (deposit_confidence           IS NULL OR (deposit_confidence           BETWEEN 0 AND 1)),
  ADD COLUMN IF NOT EXISTS single_supplement_confidence DOUBLE PRECISION
    CHECK (single_supplement_confidence IS NULL OR (single_supplement_confidence BETWEEN 0 AND 1)),
  ADD COLUMN IF NOT EXISTS visa_confidence              DOUBLE PRECISION
    CHECK (visa_confidence              IS NULL OR (visa_confidence              BETWEEN 0 AND 1)),
  ADD COLUMN IF NOT EXISTS extraction_version           TEXT;

COMMENT ON COLUMN tour_fees.tip_confidence
  IS 'Sprint 4 follow-up: per-field confidence for tip_amount; NULL → fall back to extraction_confidence.';
COMMENT ON COLUMN tour_fees.deposit_confidence
  IS 'Sprint 4 follow-up: per-field confidence for deposit_amount; NULL → fall back to extraction_confidence.';
COMMENT ON COLUMN tour_fees.single_supplement_confidence
  IS 'Sprint 4 follow-up: per-field confidence for single_supplement; NULL → fall back to extraction_confidence. Bot requires ≥ 0.90 to answer (stricter — known accuracy gap).';
COMMENT ON COLUMN tour_fees.visa_confidence
  IS 'Sprint 4 follow-up: per-field confidence for visa (status or fee); NULL → fall back to extraction_confidence.';
COMMENT ON COLUMN tour_fees.extraction_version
  IS 'Sprint 4 follow-up: pipeline version that produced this row (e.g. "1.0"); used as part of cache key with pdf_hash.';

-- Index for finding rows that need re-extraction (low per-field confidence on hardest field)
CREATE INDEX IF NOT EXISTS idx_fees_low_single_supp_confidence
  ON tour_fees(single_supplement_confidence)
  WHERE single_supplement_confidence IS NOT NULL AND single_supplement_confidence < 0.90;
