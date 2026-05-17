-- Migration 011: handoffs — human takeover events
CREATE TABLE IF NOT EXISTS handoffs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id),
  psid            TEXT NOT NULL,
  triggered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  trigger_type    TEXT NOT NULL CHECK (trigger_type IN (
    'attachment','fee_missing','human_request','payment',
    'booking_confirm','low_confidence','error','sla_breach'
  )),
  trigger_detail  JSONB NOT NULL DEFAULT '{}'::JSONB,
  bot_paused_until TIMESTAMPTZ,
  admin_responded_at TIMESTAMPTZ,
  admin_responder TEXT,
  resolution      TEXT CHECK (resolution IN ('booked','declined','no_response','bot_resumed') OR resolution IS NULL),
  resolution_at   TIMESTAMPTZ,
  notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_handoff_psid ON handoffs(psid, triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_handoff_open ON handoffs(resolution) WHERE resolution IS NULL;
CREATE INDEX IF NOT EXISTS idx_handoff_type ON handoffs(trigger_type);

ALTER TABLE handoffs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS handoff_anon_no_access ON handoffs;
CREATE POLICY handoff_anon_no_access ON handoffs FOR ALL TO anon USING (false);
DROP POLICY IF EXISTS handoff_service_role_all ON handoffs;
CREATE POLICY handoff_service_role_all ON handoffs FOR ALL TO service_role USING (true) WITH CHECK (true);
