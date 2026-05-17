-- Migration 017: extend tour_fees with full field set per Sprint 3 R2 brief
-- Additive ALTERs only — safe to re-run.

ALTER TABLE tour_fees
  ADD COLUMN IF NOT EXISTS joinland_price        INTEGER,
  ADD COLUMN IF NOT EXISTS mandatory_fees_summary TEXT,
  ADD COLUMN IF NOT EXISTS visa_status           TEXT
    CHECK (visa_status IS NULL OR visa_status IN ('exempt','required','on_arrival','evisa','unknown')),
  ADD COLUMN IF NOT EXISTS source_page           INTEGER,
  ADD COLUMN IF NOT EXISTS raw_snippet           TEXT,
  ADD COLUMN IF NOT EXISTS checked_at            TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Sprint 3 brief uses `tip_fee` / `child_no_bed_fee` as canonical names; keep
-- both the old columns (tip_amount, child_fee_no_bed) and add aliases as views
-- via comments — orchestrator + tests already use the original column names.

COMMENT ON COLUMN tour_fees.tip_amount IS 'Sprint 3 brief alias: tip_fee';
COMMENT ON COLUMN tour_fees.child_fee_no_bed IS 'Sprint 3 brief alias: child_no_bed_fee';
COMMENT ON COLUMN tour_fees.deposit_amount IS 'Sprint 3 brief alias: deposit';
COMMENT ON COLUMN tour_fees.extraction_confidence IS 'Sprint 3 brief alias: fee_confidence';

CREATE INDEX IF NOT EXISTS idx_fees_low_confidence ON tour_fees(extraction_confidence)
  WHERE extraction_confidence < 0.8;
