-- Migration 021: Detail-page departure price rows
-- DEV-2026-05-20-012 / Sprint 5 Package F
--
-- Extends tour_departures (created in migration 007) with the per-row fields
-- that are visible on the tourfiremai.com/tour/<web_code> detail price table.
-- Each ALTER is additive and idempotent (IF NOT EXISTS) so re-running this
-- migration on staging is safe.
--
-- Compatibility mapping (legacy ↔ new):
--   departure_date  ↔  departure_start
--   return_date     ↔  departure_end
--   price           ↔  adult_price
--
-- This migration is NOT applied by Claude Dev. Apply via the standard staging
-- pipeline after QA-2026-05-20-012 verdict.
--
-- Hard rules preserved:
--   - "-" in source cells is parsed by the application layer as NULL, never 0.
--   - status_text is captured verbatim; sold-out signal is owned by
--     tour_availability_overrides (migration 020), NOT by this table.
--   - web_code, tour_code_real, and airline remain distinct fields.

-- ---------------------------------------------------------------------------
-- New per-row columns
-- ---------------------------------------------------------------------------

ALTER TABLE tour_departures
  ADD COLUMN IF NOT EXISTS departure_start          DATE;
ALTER TABLE tour_departures
  ADD COLUMN IF NOT EXISTS departure_end            DATE;
ALTER TABLE tour_departures
  ADD COLUMN IF NOT EXISTS departure_label_raw      TEXT;
ALTER TABLE tour_departures
  ADD COLUMN IF NOT EXISTS bus                      INTEGER;
ALTER TABLE tour_departures
  ADD COLUMN IF NOT EXISTS adult_price              INTEGER;
ALTER TABLE tour_departures
  ADD COLUMN IF NOT EXISTS child_bed_price          INTEGER;
ALTER TABLE tour_departures
  ADD COLUMN IF NOT EXISTS child_no_bed_price       INTEGER;
ALTER TABLE tour_departures
  ADD COLUMN IF NOT EXISTS single_supplement_price  INTEGER;
ALTER TABLE tour_departures
  ADD COLUMN IF NOT EXISTS joinland_price           INTEGER;
ALTER TABLE tour_departures
  ADD COLUMN IF NOT EXISTS group_size               INTEGER;
ALTER TABLE tour_departures
  ADD COLUMN IF NOT EXISTS status_text              TEXT;
ALTER TABLE tour_departures
  ADD COLUMN IF NOT EXISTS status_class             TEXT;
ALTER TABLE tour_departures
  ADD COLUMN IF NOT EXISTS availability_status      TEXT;
ALTER TABLE tour_departures
  ADD COLUMN IF NOT EXISTS source_url               TEXT;
ALTER TABLE tour_departures
  ADD COLUMN IF NOT EXISTS tour_code_real           TEXT;

-- ---------------------------------------------------------------------------
-- Backfill mirror columns from legacy fields (idempotent, safe to re-run)
-- ---------------------------------------------------------------------------

UPDATE tour_departures
   SET departure_start = departure_date
 WHERE departure_start IS NULL
   AND departure_date IS NOT NULL;

UPDATE tour_departures
   SET departure_end = return_date
 WHERE departure_end IS NULL
   AND return_date IS NOT NULL;

UPDATE tour_departures
   SET adult_price = price
 WHERE adult_price IS NULL
   AND price IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Constraints (additive, non-breaking)
-- ---------------------------------------------------------------------------

-- All price columns must be NULL or strictly positive. "-" on the website
-- maps to NULL via the parser, never 0.
DO $$ BEGIN
  ALTER TABLE tour_departures
    ADD CONSTRAINT chk_departure_prices_nonneg CHECK (
      (adult_price             IS NULL OR adult_price             > 0) AND
      (child_bed_price         IS NULL OR child_bed_price         > 0) AND
      (child_no_bed_price      IS NULL OR child_no_bed_price      > 0) AND
      (single_supplement_price IS NULL OR single_supplement_price > 0) AND
      (joinland_price          IS NULL OR joinland_price          > 0)
    );
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

-- departure_end must be NULL or on/after departure_start.
DO $$ BEGIN
  ALTER TABLE tour_departures
    ADD CONSTRAINT chk_departure_end_after_start CHECK (
      departure_end IS NULL OR departure_start IS NULL OR
      departure_end >= departure_start
    );
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

-- availability_status is a controlled vocabulary mirroring the parser output.
DO $$ BEGIN
  ALTER TABLE tour_departures
    ADD CONSTRAINT chk_availability_status_vocab CHECK (
      availability_status IS NULL OR
      availability_status IN ('available','limited','sold_out','unknown')
    );
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

-- bus / group_size sanity bounds.
DO $$ BEGIN
  ALTER TABLE tour_departures
    ADD CONSTRAINT chk_departure_counts_nonneg CHECK (
      (bus        IS NULL OR bus        > 0) AND
      (group_size IS NULL OR group_size > 0)
    );
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

-- ---------------------------------------------------------------------------
-- Indexes for the new shape
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_dep_start
  ON tour_departures (departure_start)
  WHERE departure_start IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_dep_web_code_start
  ON tour_departures (web_code, departure_start);

-- Optional richer uniqueness across (tour_id, departure_start, departure_end,
-- COALESCE(bus,0)) so future detail-page upserts don't collide on same
-- start-date but different bus numbers. Kept as a non-unique index for now
-- to avoid a destructive change to existing rows; tightening to UNIQUE is
-- deferred until backfill is complete.
CREATE INDEX IF NOT EXISTS idx_dep_full_row
  ON tour_departures (tour_id, departure_start, departure_end, bus);
