-- Migration 012: bot_pauses — active bot-pause sessions
CREATE TABLE IF NOT EXISTS bot_pauses (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  psid            TEXT NOT NULL,
  conversation_id UUID REFERENCES conversations(id),
  paused_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  pause_until     TIMESTAMPTZ NOT NULL,
  paused_by       TEXT NOT NULL CHECK (paused_by IN ('system','admin','rule')),
  reason          TEXT,
  resumed_at      TIMESTAMPTZ,
  resumed_by      TEXT,

  CONSTRAINT chk_pause_window CHECK (pause_until > paused_at)
);

CREATE INDEX IF NOT EXISTS idx_pause_active ON bot_pauses(psid) WHERE resumed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_pause_until ON bot_pauses(pause_until) WHERE resumed_at IS NULL;

ALTER TABLE bot_pauses ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pause_anon_no_access ON bot_pauses;
CREATE POLICY pause_anon_no_access ON bot_pauses FOR ALL TO anon USING (false);
DROP POLICY IF EXISTS pause_service_role_all ON bot_pauses;
CREATE POLICY pause_service_role_all ON bot_pauses FOR ALL TO service_role USING (true) WITH CHECK (true);
