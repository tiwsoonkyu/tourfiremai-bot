-- Migration 006: tours_canonical — canonical tour database
-- CRITICAL: web_code, tour_code_real, airline are SEPARATE fields (V1 bug fix)
CREATE TABLE IF NOT EXISTS tours_canonical (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  web_code        TEXT UNIQUE NOT NULL,        -- e.g. ap242455
  tour_code_real  TEXT,                         -- e.g. BCCKG27-HU
  name            TEXT NOT NULL,
  country         TEXT NOT NULL,
  country_id      INTEGER NOT NULL,
  city_tags       TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
  days            INTEGER NOT NULL,
  nights          INTEGER NOT NULL,
  base_price      INTEGER NOT NULL,
  airline         TEXT,                         -- e.g. HU, VZ, XJ, TG
  wholesale       TEXT,                         -- e.g. GS, TTN, Best, Zego
  url             TEXT NOT NULL,
  pdf_url         TEXT,

  description     TEXT,
  highlights      TEXT[] NOT NULL DEFAULT '{}'::TEXT[],

  is_active       BOOLEAN NOT NULL DEFAULT TRUE,
  is_fire_sale    BOOLEAN NOT NULL DEFAULT FALSE,

  scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_synced_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT chk_codes_differ CHECK (
    tour_code_real IS NULL OR tour_code_real <> web_code
  ),
  CONSTRAINT chk_airline_not_code CHECK (
    airline IS NULL OR LENGTH(airline) <= 4
  )
);

CREATE INDEX IF NOT EXISTS idx_tours_country ON tours_canonical(country_id) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_tours_price ON tours_canonical(base_price) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_tours_web_code ON tours_canonical(web_code);
CREATE INDEX IF NOT EXISTS idx_tours_real_code ON tours_canonical(tour_code_real)
WHERE tour_code_real IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tours_fire_sale ON tours_canonical(is_fire_sale)
WHERE is_fire_sale = TRUE;
CREATE INDEX IF NOT EXISTS idx_tours_city_tags_gin ON tours_canonical USING GIN (city_tags);

ALTER TABLE tours_canonical ENABLE ROW LEVEL SECURITY;
-- tours are non-PII catalog data — anon CAN read for future public read endpoint, but write is service_role only
DROP POLICY IF EXISTS tours_anon_read_only ON tours_canonical;
CREATE POLICY tours_anon_read_only ON tours_canonical FOR SELECT TO anon USING (is_active = TRUE);
DROP POLICY IF EXISTS tours_service_role_all ON tours_canonical;
CREATE POLICY tours_service_role_all ON tours_canonical FOR ALL TO service_role USING (true) WITH CHECK (true);
