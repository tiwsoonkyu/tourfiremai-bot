-- ============================================================
-- TourFiremai CRM Schema — Supabase
-- Run this in Supabase SQL Editor (Dashboard → SQL Editor)
-- ============================================================

-- Leads table (one row per FB Messenger user / PSID)
CREATE TABLE IF NOT EXISTS leads (
    id                BIGSERIAL PRIMARY KEY,
    psid              TEXT NOT NULL UNIQUE,        -- Facebook Page-Scoped ID
    customer_name     TEXT,
    phone             TEXT,                        -- เบอร์โทร หรือ LINE ID
    destination       TEXT,                        -- เมือง/ปลายทาง เช่น โอซาก้า
    country           TEXT,                        -- ชื่อประเทศ เช่น ญี่ปุ่น
    month             TEXT,                        -- เดือนที่จะไป เช่น ก.ค. 69
    budget_per_person INTEGER,                     -- งบต่อคน (บาท)
    pax               INTEGER,                     -- จำนวนคน
    lead_stage        TEXT DEFAULT 'cold'          -- cold | warm | hot | booking
                      CHECK (lead_stage IN ('cold','warm','hot','booking')),
    last_options      JSONB,                       -- โปรแกรมล่าสุดที่ AI เสนอ
    last_message      TEXT,                        -- ข้อความล่าสุดของลูกค้า
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);

-- Index สำหรับ query บ่อย
CREATE INDEX IF NOT EXISTS idx_leads_stage ON leads (lead_stage);
CREATE INDEX IF NOT EXISTS idx_leads_updated ON leads (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_country ON leads (country);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS leads_updated_at ON leads;
CREATE TRIGGER leads_updated_at
  BEFORE UPDATE ON leads
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Enable RLS (Row Level Security) - ป้องกันการเข้าถึงโดยตรงจาก client
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;

-- Policy: allow service_role (backend) ทุก operation
CREATE POLICY "Service role full access" ON leads
  FOR ALL USING (auth.role() = 'service_role');

-- ============================================================
-- ตัวอย่าง query สำหรับ Dashboard
-- ============================================================
-- SELECT lead_stage, COUNT(*) FROM leads GROUP BY lead_stage;
-- SELECT * FROM leads WHERE lead_stage IN ('hot','booking') ORDER BY updated_at DESC LIMIT 20;
-- SELECT * FROM leads WHERE country = 'ญี่ปุ่น' AND lead_stage != 'cold';
