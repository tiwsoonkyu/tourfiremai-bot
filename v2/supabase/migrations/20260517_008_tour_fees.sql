-- Migration 008: tour_fees — PDF-extracted fees (Sprint 4 populates)
CREATE TABLE IF NOT EXISTS tour_fees (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tour_id         UUID NOT NULL REFERENCES tours_canonical(id) ON DELETE CASCADE,
  tour_code_real  TEXT NOT NULL,
  pdf_url         TEXT NOT NULL,
  pdf_hash        TEXT,

  tip_amount        INTEGER,
  visa_fee          INTEGER,
  single_supplement INTEGER,
  infant_fee        INTEGER,
  child_fee_no_bed  INTEGER,
  deposit_amount    INTEGER,

  other_fees      JSONB NOT NULL DEFAULT '{}'::JSONB,

  extraction_method     TEXT NOT NULL CHECK (extraction_method IN ('pdfplumber','regex','ocr','llm_vision','manual')),
  extraction_confidence REAL NOT NULL DEFAULT 0.0 CHECK (extraction_confidence BETWEEN 0 AND 1),
  extraction_errors     TEXT[] NOT NULL DEFAULT '{}'::TEXT[],

  extracted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  manually_verified BOOLEAN NOT NULL DEFAULT FALSE,
  verified_by       TEXT,
  verified_at       TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_fees_tour ON tour_fees(tour_id);
CREATE INDEX IF NOT EXISTS idx_fees_unverified ON tour_fees(manually_verified) WHERE manually_verified = FALSE;

ALTER TABLE tour_fees ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS fees_anon_no_access ON tour_fees;
CREATE POLICY fees_anon_no_access ON tour_fees FOR ALL TO anon USING (false);
DROP POLICY IF EXISTS fees_service_role_all ON tour_fees;
CREATE POLICY fees_service_role_all ON tour_fees FOR ALL TO service_role USING (true) WITH CHECK (true);
