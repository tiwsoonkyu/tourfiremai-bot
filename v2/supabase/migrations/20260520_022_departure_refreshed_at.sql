-- Migration 022: tour_departures freshness column
-- DEV-2026-05-20-015 / Sprint 5 Package I
--
-- Adds a nullable refreshed_at timestamptz column to tour_departures so the
-- orchestrator + detail-row read path can refuse to serve stale rows and
-- trigger a deterministic re-fetch instead.
--
-- Hard rules preserved:
--   - Additive, idempotent. Safe to re-run.
--   - Does NOT drop, mutate, or backfill historical row contents (besides
--     populating the new freshness column from an existing audit column when
--     present — the backfill is a NULL-safe coalesce, never an overwrite).
--   - NOT applied by Claude Dev. Apply via the standard staging pipeline
--     after QA-2026-05-20-015 verdict.
--   - This migration does NOT make the (tour_id, departure_start,
--     departure_end, COALESCE(bus,0)) key UNIQUE. The UNIQUE step is
--     deferred until docs/V2_DEPARTURE_UNIQUENESS_PROPOSAL.md +
--     v2.tools.departure_duplicate_audit returns zero duplicates on staging.

-- ---------------------------------------------------------------------------
-- New nullable freshness column
-- ---------------------------------------------------------------------------

ALTER TABLE tour_departures
  ADD COLUMN IF NOT EXISTS refreshed_at TIMESTAMPTZ;

-- Best-effort initial population so existing rows are not seen as "never
-- refreshed" the moment the freshness gate ships. We coalesce to the most
-- recent of (updated_at, scraped_at, created_at) when those columns exist.
DO $$ BEGIN
  UPDATE tour_departures
     SET refreshed_at = COALESCE(refreshed_at, updated_at)
   WHERE refreshed_at IS NULL
     AND updated_at IS NOT NULL;
EXCEPTION
  WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
  UPDATE tour_departures
     SET refreshed_at = COALESCE(refreshed_at, scraped_at)
   WHERE refreshed_at IS NULL
     AND scraped_at IS NOT NULL;
EXCEPTION
  WHEN undefined_column THEN NULL;
END $$;

DO $$ BEGIN
  UPDATE tour_departures
     SET refreshed_at = COALESCE(refreshed_at, created_at)
   WHERE refreshed_at IS NULL
     AND created_at IS NOT NULL;
EXCEPTION
  WHEN undefined_column THEN NULL;
END $$;

-- ---------------------------------------------------------------------------
-- Helper index — refreshed-at ordering for stale-row detection queries.
-- Not unique, partial to skip NULL refreshed_at rows.
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_dep_refreshed_at
  ON tour_departures (refreshed_at)
  WHERE refreshed_at IS NOT NULL;
