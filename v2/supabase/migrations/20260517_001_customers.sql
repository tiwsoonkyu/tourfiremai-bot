-- Migration 001: customers — long-term customer profile (PII container)
-- Note: anon role MUST NOT read this table. Service role only.

CREATE TABLE IF NOT EXISTS customers (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  psid            TEXT UNIQUE NOT NULL,
  fb_name         TEXT,
  first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  preferred_country  TEXT,
  preferred_budget   INTEGER,
  preferred_pax      INTEGER,
  preferred_period   TEXT,
  preferred_airline  TEXT,

  total_conversations INTEGER NOT NULL DEFAULT 0,
  total_bookings      INTEGER NOT NULL DEFAULT 0,
  last_booking_at     TIMESTAMPTZ,
  customer_tier       TEXT NOT NULL DEFAULT 'new' CHECK (customer_tier IN ('new','active','loyal','dormant','blocked')),

  pdpa_consent_at     TIMESTAMPTZ,
  pdpa_consent_text   TEXT,

  notes               TEXT,
  tags                TEXT[] NOT NULL DEFAULT '{}'::TEXT[]
);

CREATE INDEX IF NOT EXISTS idx_customers_psid ON customers(psid);
CREATE INDEX IF NOT EXISTS idx_customers_last_seen ON customers(last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_customers_tier ON customers(customer_tier);

-- RLS draft: enable + restrictive policy. Anon cannot read.
ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS customers_anon_no_access ON customers;
CREATE POLICY customers_anon_no_access ON customers FOR ALL TO anon USING (false);
DROP POLICY IF EXISTS customers_service_role_all ON customers;
CREATE POLICY customers_service_role_all ON customers FOR ALL TO service_role USING (true) WITH CHECK (true);
