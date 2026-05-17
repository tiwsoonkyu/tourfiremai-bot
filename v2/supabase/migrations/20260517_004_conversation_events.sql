-- Migration 004: conversation_events — immutable event log + idempotency
CREATE TABLE IF NOT EXISTS conversation_events (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  psid            TEXT NOT NULL,
  event_type      TEXT NOT NULL,
  event_data      JSONB NOT NULL DEFAULT '{}'::JSONB,
  triggered_by    TEXT NOT NULL CHECK (triggered_by IN ('bot','customer_message','admin','cron','system')),
  related_turn_id UUID,
  meta_message_id TEXT,
  platform        TEXT NOT NULL DEFAULT 'fb' CHECK (platform IN ('fb','line','web')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cevents_conv ON conversation_events(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_cevents_psid_time ON conversation_events(psid, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cevents_type ON conversation_events(event_type);

-- Idempotency: same platform+meta_message_id cannot be inserted twice
CREATE UNIQUE INDEX IF NOT EXISTS idx_cevents_dedup ON conversation_events(platform, meta_message_id)
WHERE meta_message_id IS NOT NULL;

ALTER TABLE conversation_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cevents_anon_no_access ON conversation_events;
CREATE POLICY cevents_anon_no_access ON conversation_events FOR ALL TO anon USING (false);
DROP POLICY IF EXISTS cevents_service_role_all ON conversation_events;
CREATE POLICY cevents_service_role_all ON conversation_events FOR ALL TO service_role USING (true) WITH CHECK (true);
