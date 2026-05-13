-- Tour State Engine: Offer Snapshots table
-- Run this in Supabase SQL editor before deploying the new bot version

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

-- Auto-delete expired snapshots (optional, run periodically)
-- DELETE FROM offer_snapshots WHERE expires_at < NOW();

COMMENT ON TABLE offer_snapshots IS
    'Stores deterministic offer snapshots for tour selection state machine. TTL 30 days.';
