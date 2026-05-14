-- Tour State Engine: Offer Snapshots table
-- Run this in Supabase SQL editor (Dashboard → SQL Editor → New Query)
-- Updated: includes explicit GRANTs for Supabase API policy change (effective Oct 30, 2025)

CREATE TABLE IF NOT EXISTS offer_snapshots (
    id          BIGSERIAL PRIMARY KEY,
    psid        TEXT        NOT NULL,
    offer_set_id TEXT       NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '30 days',
    search_context JSONB,
    options     JSONB,
    UNIQUE(psid, offer_set_id)
);

CREATE INDEX IF NOT EXISTS offer_snapshots_psid_created
    ON offer_snapshots (psid, created_at DESC);

CREATE INDEX IF NOT EXISTS offer_snapshots_expires
    ON offer_snapshots (expires_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- IMPORTANT: Explicit GRANTs required for Supabase Data API (PostgREST)
-- Starting May 30 (new projects) / Oct 30 (existing projects), new tables
-- in the public schema need explicit grants to be accessible via REST API.
-- The bot uses the service_role key → grant to service_role is sufficient.
-- ─────────────────────────────────────────────────────────────────────────────

-- Allow REST API (via service_role key) to read/write
GRANT ALL ON TABLE offer_snapshots TO service_role;
GRANT ALL ON SEQUENCE offer_snapshots_id_seq TO service_role;

-- Allow authenticated users (if ever needed)
GRANT SELECT, INSERT ON TABLE offer_snapshots TO authenticated;
GRANT USAGE ON SEQUENCE offer_snapshots_id_seq TO authenticated;

-- Enable Row Level Security (best practice — then add policy)
ALTER TABLE offer_snapshots ENABLE ROW LEVEL SECURITY;

-- RLS policy: service_role bypasses RLS automatically
-- Allow the bot (service_role) full access
CREATE POLICY IF NOT EXISTS "service_role_full_access"
    ON offer_snapshots
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

COMMENT ON TABLE offer_snapshots IS
    'Stores deterministic offer snapshots for tour selection state machine. TTL 30 days.';
