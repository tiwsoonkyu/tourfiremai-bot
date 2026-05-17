-- Migration 014: tool_calls — every deterministic tool invocation
CREATE TABLE IF NOT EXISTS tool_calls (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_run_id    UUID REFERENCES agent_runs(id) ON DELETE CASCADE,
  trace_id        UUID NOT NULL,
  conversation_id UUID,
  psid            TEXT,

  tool_name       TEXT NOT NULL,
  caller          TEXT CHECK (caller IN ('orchestrator','llm','rule_engine','test')),
  input           JSONB,
  output_summary  JSONB,
  status          TEXT NOT NULL CHECK (status IN ('success','error','timeout','skipped')),
  error_code      TEXT,
  error_message   TEXT,
  duration_ms     INTEGER,
  cache_hit       BOOLEAN NOT NULL DEFAULT FALSE,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tools_run ON tool_calls(agent_run_id);
CREATE INDEX IF NOT EXISTS idx_tools_psid_time ON tool_calls(psid, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tools_name_status ON tool_calls(tool_name, status);
CREATE INDEX IF NOT EXISTS idx_tools_errors ON tool_calls(status) WHERE status <> 'success';

ALTER TABLE tool_calls ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tools_anon_no_access ON tool_calls;
CREATE POLICY tools_anon_no_access ON tool_calls FOR ALL TO anon USING (false);
DROP POLICY IF EXISTS tools_service_role_all ON tool_calls;
CREATE POLICY tools_service_role_all ON tool_calls FOR ALL TO service_role USING (true) WITH CHECK (true);
