-- Migration 015: dlq_messages — Dead Letter Queue for poison messages
CREATE TABLE IF NOT EXISTS dlq_messages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  platform        TEXT NOT NULL CHECK (platform IN ('fb','line','web')),
  meta_message_id TEXT NOT NULL,
  psid            TEXT NOT NULL,
  raw_payload     JSONB NOT NULL,
  failure_count   INTEGER NOT NULL,
  last_error      TEXT,
  last_traceback  TEXT,
  first_failed_at TIMESTAMPTZ NOT NULL,
  last_failed_at  TIMESTAMPTZ NOT NULL,
  resolved        BOOLEAN NOT NULL DEFAULT FALSE,
  resolved_at     TIMESTAMPTZ,
  resolution      TEXT CHECK (resolution IN ('manual_replay','discarded','fixed_in_code') OR resolution IS NULL),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dlq_unresolved ON dlq_messages(resolved) WHERE resolved = FALSE;
CREATE INDEX IF NOT EXISTS idx_dlq_psid ON dlq_messages(psid, last_failed_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dlq_unique_msg ON dlq_messages(platform, meta_message_id);

ALTER TABLE dlq_messages ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS dlq_anon_no_access ON dlq_messages;
CREATE POLICY dlq_anon_no_access ON dlq_messages FOR ALL TO anon USING (false);
DROP POLICY IF EXISTS dlq_service_role_all ON dlq_messages;
CREATE POLICY dlq_service_role_all ON dlq_messages FOR ALL TO service_role USING (true) WITH CHECK (true);
