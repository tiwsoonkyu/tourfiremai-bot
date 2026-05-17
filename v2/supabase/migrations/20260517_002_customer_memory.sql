-- Migration 002: customer_memory — bot fast-access snapshot (wide table)
-- Mirrors latest convo state — service role only.

CREATE TABLE IF NOT EXISTS customer_memory (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id              UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  psid                     TEXT UNIQUE NOT NULL,
  customer_name            TEXT,

  latest_country           TEXT,
  latest_city              TEXT,
  budget_per_person        INTEGER,
  budget_type              TEXT CHECK (budget_type IN ('strict','flexible','unknown') OR budget_type IS NULL),
  travel_month             TEXT,
  pax_count                INTEGER,
  airline_preference       TEXT,

  selected_tour_web_code   TEXT,
  selected_tour_code_real  TEXT,
  latest_offer_set_id      UUID,
  conversation_state       TEXT,

  updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_by               TEXT NOT NULL DEFAULT 'bot' CHECK (updated_by IN ('bot','admin','sync_cron','system')),
  last_update_reason       TEXT
);

CREATE INDEX IF NOT EXISTS idx_cmem_customer ON customer_memory(customer_id);
CREATE INDEX IF NOT EXISTS idx_cmem_state ON customer_memory(conversation_state);
CREATE INDEX IF NOT EXISTS idx_cmem_updated ON customer_memory(updated_at DESC);

ALTER TABLE customer_memory ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cmem_anon_no_access ON customer_memory;
CREATE POLICY cmem_anon_no_access ON customer_memory FOR ALL TO anon USING (false);
DROP POLICY IF EXISTS cmem_service_role_all ON customer_memory;
CREATE POLICY cmem_service_role_all ON customer_memory FOR ALL TO service_role USING (true) WITH CHECK (true);
