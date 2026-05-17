-- Migration 010: selected_tours — locked customer selections
CREATE TABLE IF NOT EXISTS selected_tours (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  customer_id     UUID NOT NULL REFERENCES customers(id),
  psid            TEXT NOT NULL,
  tour_id         UUID NOT NULL REFERENCES tours_canonical(id),
  tour_code_real  TEXT,
  selected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  unlocked_at     TIMESTAMPTZ,
  unlock_reason   TEXT,

  departure_date_chosen DATE,
  pax_confirmed   INTEGER,
  is_fee_acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
  booking_status  TEXT NOT NULL DEFAULT 'considering' CHECK (booking_status IN ('considering','handoff','booked','lost'))
);

-- One active lock per PSID at a time
CREATE UNIQUE INDEX IF NOT EXISTS idx_selected_one_active_per_psid
  ON selected_tours(psid) WHERE unlocked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_selected_conv ON selected_tours(conversation_id);
CREATE INDEX IF NOT EXISTS idx_selected_customer ON selected_tours(customer_id, selected_at DESC);

ALTER TABLE selected_tours ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS selected_anon_no_access ON selected_tours;
CREATE POLICY selected_anon_no_access ON selected_tours FOR ALL TO anon USING (false);
DROP POLICY IF EXISTS selected_service_role_all ON selected_tours;
CREATE POLICY selected_service_role_all ON selected_tours FOR ALL TO service_role USING (true) WITH CHECK (true);
