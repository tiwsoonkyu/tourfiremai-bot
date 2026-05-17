-- Migration 013: agent_runs — per-turn agent execution log
CREATE TABLE IF NOT EXISTS agent_runs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID REFERENCES conversations(id),
  psid            TEXT NOT NULL,
  turn_number     INTEGER,
  trace_id        UUID NOT NULL,

  agent_name      TEXT NOT NULL,
  state_before    TEXT,
  state_after     TEXT,

  llm_model       TEXT,
  llm_tokens_in   INTEGER,
  llm_tokens_out  INTEGER,
  llm_latency_ms  INTEGER,

  decision        TEXT,
  decision_data   JSONB,
  errors          JSONB,
  duration_ms     INTEGER,

  meta_message_id TEXT,
  platform        TEXT NOT NULL DEFAULT 'fb',

  started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_runs_psid ON agent_runs(psid, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_trace ON agent_runs(trace_id);
CREATE INDEX IF NOT EXISTS idx_runs_agent ON agent_runs(agent_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_errors ON agent_runs((errors IS NOT NULL)) WHERE errors IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_runs_message ON agent_runs(platform, meta_message_id) WHERE meta_message_id IS NOT NULL;

ALTER TABLE agent_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS runs_anon_no_access ON agent_runs;
CREATE POLICY runs_anon_no_access ON agent_runs FOR ALL TO anon USING (false);
DROP POLICY IF EXISTS runs_service_role_all ON agent_runs;
CREATE POLICY runs_service_role_all ON agent_runs FOR ALL TO service_role USING (true) WITH CHECK (true);
