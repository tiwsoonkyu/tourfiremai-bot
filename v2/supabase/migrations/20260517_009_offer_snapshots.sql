-- Migration 009: offer_snapshots — every Top N presentation, immutable
CREATE TABLE IF NOT EXISTS offer_snapshots (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  psid            TEXT NOT NULL,
  presented_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  context         JSONB NOT NULL DEFAULT '{}'::JSONB,

  tour_list       JSONB NOT NULL,

  was_selected    BOOLEAN NOT NULL DEFAULT FALSE,
  selected_rank   INTEGER,
  selected_tour_id UUID,

  CONSTRAINT chk_tour_list_array CHECK (jsonb_typeof(tour_list) = 'array' AND jsonb_array_length(tour_list) > 0)
);

CREATE INDEX IF NOT EXISTS idx_offers_psid ON offer_snapshots(psid, presented_at DESC);
CREATE INDEX IF NOT EXISTS idx_offers_conv ON offer_snapshots(conversation_id);
CREATE INDEX IF NOT EXISTS idx_offers_unselected ON offer_snapshots(presented_at DESC) WHERE was_selected = FALSE;

ALTER TABLE offer_snapshots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS offers_anon_no_access ON offer_snapshots;
CREATE POLICY offers_anon_no_access ON offer_snapshots FOR ALL TO anon USING (false);
DROP POLICY IF EXISTS offers_service_role_all ON offer_snapshots;
CREATE POLICY offers_service_role_all ON offer_snapshots FOR ALL TO service_role USING (true) WITH CHECK (true);
