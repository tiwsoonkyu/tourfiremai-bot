-- Migration 005: conversation_turns — every customer/bot message + idempotency
CREATE TABLE IF NOT EXISTS conversation_turns (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  psid            TEXT NOT NULL,
  turn_number     INTEGER NOT NULL,
  direction       TEXT NOT NULL CHECK (direction IN ('inbound','outbound','system')),
  speaker         TEXT NOT NULL CHECK (speaker IN ('customer','bot','admin')),
  message_text    TEXT,
  attachments     JSONB,
  state_before    TEXT,
  state_after     TEXT,
  intent          JSONB,
  tool_calls      JSONB,
  llm_model       TEXT,
  llm_tokens_in   INTEGER,
  llm_tokens_out  INTEGER,
  latency_ms      INTEGER,
  meta_message_id TEXT,
  platform        TEXT NOT NULL DEFAULT 'fb',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_turns_conv ON conversation_turns(conversation_id, turn_number);
CREATE INDEX IF NOT EXISTS idx_turns_psid_time ON conversation_turns(psid, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_turns_direction ON conversation_turns(direction);
CREATE UNIQUE INDEX IF NOT EXISTS idx_turns_dedup ON conversation_turns(platform, meta_message_id)
WHERE meta_message_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_turns_unique_in_conv ON conversation_turns(conversation_id, turn_number);

ALTER TABLE conversation_turns ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS turns_anon_no_access ON conversation_turns;
CREATE POLICY turns_anon_no_access ON conversation_turns FOR ALL TO anon USING (false);
DROP POLICY IF EXISTS turns_service_role_all ON conversation_turns;
CREATE POLICY turns_service_role_all ON conversation_turns FOR ALL TO service_role USING (true) WITH CHECK (true);
