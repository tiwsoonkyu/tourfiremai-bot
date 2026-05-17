-- Migration 003: conversations — per-thread state machine row
CREATE TABLE IF NOT EXISTS conversations (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id     UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  psid            TEXT NOT NULL,
  state           TEXT NOT NULL DEFAULT 'new_lead' CHECK (state IN (
    'new_lead','collecting_preferences','options_presented',
    'tour_selected','departure_selected','fee_check_required',
    'booking_ready_for_handoff','waiting_team','human_paused','closed'
  )),
  started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  closed_at       TIMESTAMPTZ,
  close_reason    TEXT,

  current_country         TEXT,
  current_budget          INTEGER,
  current_budget_type     TEXT,
  current_pax             INTEGER,
  current_period          TEXT,
  current_offer_id        UUID,
  selected_tour_id        UUID,
  selected_departure_date DATE,

  is_human_paused   BOOLEAN NOT NULL DEFAULT FALSE,
  paused_until      TIMESTAMPTZ,
  paused_reason     TEXT,
  waiting_ack_sent  BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_conv_psid ON conversations(psid);
CREATE INDEX IF NOT EXISTS idx_conv_state ON conversations(state);
CREATE INDEX IF NOT EXISTS idx_conv_active ON conversations(last_activity_at DESC) WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_conv_human_paused ON conversations(is_human_paused) WHERE is_human_paused = TRUE;
CREATE UNIQUE INDEX IF NOT EXISTS idx_conv_one_active_per_psid ON conversations(psid) WHERE closed_at IS NULL;

ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS conv_anon_no_access ON conversations;
CREATE POLICY conv_anon_no_access ON conversations FOR ALL TO anon USING (false);
DROP POLICY IF EXISTS conv_service_role_all ON conversations;
CREATE POLICY conv_service_role_all ON conversations FOR ALL TO service_role USING (true) WITH CHECK (true);
