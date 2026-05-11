-- ─────────────────────────────────────────────────────────────────────────────
-- Migration v3: PDF Fee Extraction Columns
-- รัน 1 ครั้งใน Supabase SQL Editor
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. เพิ่ม PDF fee extraction columns ใน tours table
ALTER TABLE tours
  ADD COLUMN IF NOT EXISTS pdf_url                 TEXT,
  ADD COLUMN IF NOT EXISTS deposit                 INT,
  ADD COLUMN IF NOT EXISTS infant_fee              INT,
  ADD COLUMN IF NOT EXISTS child_no_bed_fee        INT,
  ADD COLUMN IF NOT EXISTS mandatory_fees_summary  TEXT,
  ADD COLUMN IF NOT EXISTS fee_extraction_status   TEXT,  -- found / partial / not_found / error
  ADD COLUMN IF NOT EXISTS fee_confidence          TEXT,  -- high / medium / low
  ADD COLUMN IF NOT EXISTS fee_checked_at          TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS fee_source_page         INT,
  ADD COLUMN IF NOT EXISTS fee_raw_snippet         TEXT;

-- 2. Index สำหรับ fee extractor query
CREATE INDEX IF NOT EXISTS tours_fee_status_idx
  ON tours(fee_extraction_status)
  WHERE is_active = true;

CREATE INDEX IF NOT EXISTS tours_fee_null_idx
  ON tours(id)
  WHERE fee_extraction_status IS NULL AND is_active = true;

-- 3. fee_extraction_runs — log ทุก batch run
CREATE TABLE IF NOT EXISTS fee_extraction_runs (
  id              BIGSERIAL PRIMARY KEY,
  started_at      TIMESTAMPTZ DEFAULT NOW(),
  finished_at     TIMESTAMPTZ,
  total_processed INT DEFAULT 0,
  total_found     INT DEFAULT 0,
  total_partial   INT DEFAULT 0,
  total_not_found INT DEFAULT 0,
  total_error     INT DEFAULT 0,
  force_recheck   BOOLEAN DEFAULT false,
  status          TEXT,
  error_message   TEXT
);

-- 4. ตรวจสอบ columns ที่เพิ่มมา
-- SELECT column_name, data_type
-- FROM information_schema.columns
-- WHERE table_name = 'tours'
--   AND column_name IN ('pdf_url','deposit','infant_fee','child_no_bed_fee',
--                       'mandatory_fees_summary','fee_extraction_status',
--                       'fee_confidence','fee_checked_at','fee_source_page','fee_raw_snippet')
-- ORDER BY column_name;
