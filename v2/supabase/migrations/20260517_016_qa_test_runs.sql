-- Migration 016: qa_test_runs — automated test result tracking
CREATE TABLE IF NOT EXISTS qa_test_runs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  test_suite      TEXT NOT NULL CHECK (test_suite IN ('unit','integration','e2e','load','security')),
  test_name       TEXT NOT NULL,
  test_file       TEXT,
  status          TEXT NOT NULL CHECK (status IN ('pass','fail','skip','error')),
  duration_ms     INTEGER,
  error_message   TEXT,
  stack_trace     TEXT,

  git_commit      TEXT,
  branch          TEXT,
  triggered_by    TEXT CHECK (triggered_by IN ('ci','manual','pre_deploy')),
  env             TEXT CHECK (env IN ('dev','staging','prod')),

  artifacts       JSONB,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_qa_suite ON qa_test_runs(test_suite, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qa_fail ON qa_test_runs(status, created_at DESC) WHERE status IN ('fail','error');
CREATE INDEX IF NOT EXISTS idx_qa_commit ON qa_test_runs(git_commit);

ALTER TABLE qa_test_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS qa_anon_no_access ON qa_test_runs;
CREATE POLICY qa_anon_no_access ON qa_test_runs FOR ALL TO anon USING (false);
DROP POLICY IF EXISTS qa_service_role_all ON qa_test_runs;
CREATE POLICY qa_service_role_all ON qa_test_runs FOR ALL TO service_role USING (true) WITH CHECK (true);
