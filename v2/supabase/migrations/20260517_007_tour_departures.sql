-- Migration 007: tour_departures — per-departure date info
CREATE TABLE IF NOT EXISTS tour_departures (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tour_id         UUID NOT NULL REFERENCES tours_canonical(id) ON DELETE CASCADE,
  web_code        TEXT NOT NULL,
  departure_date  DATE NOT NULL,
  return_date     DATE,
  price           INTEGER,
  seats_available INTEGER,
  status          TEXT NOT NULL DEFAULT 'available' CHECK (status IN ('available','limited','sold_out','cancelled')),
  airline         TEXT,
  scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT chk_return_after_dep CHECK (return_date IS NULL OR return_date >= departure_date)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_dep_unique ON tour_departures(tour_id, departure_date);
CREATE INDEX IF NOT EXISTS idx_dep_web_code ON tour_departures(web_code);
CREATE INDEX IF NOT EXISTS idx_dep_date ON tour_departures(departure_date) WHERE status = 'available';
CREATE INDEX IF NOT EXISTS idx_dep_status ON tour_departures(status);

ALTER TABLE tour_departures ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS dep_anon_read_only ON tour_departures;
CREATE POLICY dep_anon_read_only ON tour_departures FOR SELECT TO anon USING (true);
DROP POLICY IF EXISTS dep_service_role_all ON tour_departures;
CREATE POLICY dep_service_role_all ON tour_departures FOR ALL TO service_role USING (true) WITH CHECK (true);
