-- ============================================================
-- TourFireMai Dashboard Migration — v1
-- Run in Supabase SQL Editor (idempotent — safe to re-run)
-- ============================================================

-- ────────────────────────────────────────────────────────────
-- 1. Extend leads table — เพิ่ม columns สำหรับ Dashboard
--    (ไม่แตะ columns เดิม — additive only)
-- ────────────────────────────────────────────────────────────

-- ── Fix lead_stage CHECK constraint — เพิ่ม paid / awaiting_docs / complete ──
-- constraint เดิมมีแค่ cold|warm|hot|booking แต่บอทใช้ paid, awaiting_docs, complete ด้วย
ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_lead_stage_check;
ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_lead_stage_check1;
ALTER TABLE leads ADD CONSTRAINT leads_lead_stage_check
    CHECK (lead_stage IN ('cold','warm','hot','booking','paid','awaiting_docs','complete'));

-- Status ของ lead (operational workflow)
ALTER TABLE leads ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'open';
ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_status_check;
ALTER TABLE leads ADD CONSTRAINT leads_status_check
    CHECK (status IN ('open','waiting_customer','waiting_team','contacted','booked','lost'));

-- Channel ที่ lead เข้ามา
ALTER TABLE leads ADD COLUMN IF NOT EXISTS channel TEXT DEFAULT 'messenger';

-- Handoff flag — bot ส่ง notify ให้เซลล์แล้ว
ALTER TABLE leads ADD COLUMN IF NOT EXISTS handoff_requested BOOLEAN DEFAULT false;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS handoff_at TIMESTAMPTZ;

-- Needs Review — bot ไม่มั่นใจ / fallback / ไม่เจอทัวร์
ALTER TABLE leads ADD COLUMN IF NOT EXISTS needs_review BOOLEAN DEFAULT false;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS review_reason TEXT;

-- Tour code fields ที่แยกออกมาชัดเจน
ALTER TABLE leads ADD COLUMN IF NOT EXISTS selected_tour_code_real TEXT;   -- เช่น ZGNRT-2618VZ
ALTER TABLE leads ADD COLUMN IF NOT EXISTS selected_web_code TEXT;         -- เช่น ap241533
ALTER TABLE leads ADD COLUMN IF NOT EXISTS selected_tour_airline TEXT;     -- เช่น VZ

-- Ad attribution shortcut บน leads row
ALTER TABLE leads ADD COLUMN IF NOT EXISTS ad_ref TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS ad_title TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS ad_id TEXT;

-- Last bot reply (สำหรับ Inbox Monitor)
ALTER TABLE leads ADD COLUMN IF NOT EXISTS last_bot_message TEXT;

-- Indexes สำหรับ dashboard queries
CREATE INDEX IF NOT EXISTS idx_leads_status        ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_needs_review  ON leads(needs_review);
CREATE INDEX IF NOT EXISTS idx_leads_handoff       ON leads(handoff_requested);
CREATE INDEX IF NOT EXISTS idx_leads_created       ON leads(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_ad_id         ON leads(ad_id);

-- ────────────────────────────────────────────────────────────
-- 2. ai_chat_events — event log ทุก turn ที่สำคัญ
-- ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ai_chat_events (
    id                      BIGSERIAL PRIMARY KEY,
    psid                    TEXT NOT NULL,
    customer_id             BIGINT,
    lead_id                 BIGINT,
    -- Event type: user_message | bot_reply | search_result | handoff |
    --             no_tour_found | selected_tour | booking_intent |
    --             pdf_fail | fallback | ad_entry
    event_type              TEXT NOT NULL,
    role                    TEXT CHECK (role IN ('user','assistant','system')),
    message                 TEXT,
    bot_reply               TEXT,
    intent                  TEXT,         -- track a/b/c / action type
    lead_stage              TEXT,
    -- Destination info
    destination             TEXT,
    country                 TEXT,
    country_id              TEXT,
    city_hint               TEXT,
    -- Tour selection
    selected_tour_code_real TEXT,         -- ZGNRT-2618VZ
    selected_web_code       TEXT,         -- ap241533
    selected_tour_name      TEXT,
    selected_tour_url       TEXT,
    selected_tour_airline   TEXT,
    selected_tour_price     TEXT,
    -- Ad source
    ad_id                   TEXT,
    ad_ref                  TEXT,
    ad_title                TEXT,
    -- Review flags
    needs_review            BOOLEAN DEFAULT false,
    review_reason           TEXT,
    -- Extra
    metadata                JSONB,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ai_chat_events_created_idx     ON ai_chat_events(created_at DESC);
CREATE INDEX IF NOT EXISTS ai_chat_events_psid_idx        ON ai_chat_events(psid);
CREATE INDEX IF NOT EXISTS ai_chat_events_event_type_idx  ON ai_chat_events(event_type);
CREATE INDEX IF NOT EXISTS ai_chat_events_lead_stage_idx  ON ai_chat_events(lead_stage);
CREATE INDEX IF NOT EXISTS ai_chat_events_needs_review_idx ON ai_chat_events(needs_review);
CREATE INDEX IF NOT EXISTS ai_chat_events_ad_id_idx       ON ai_chat_events(ad_id);

-- ────────────────────────────────────────────────────────────
-- 3. RLS Policies — อนุญาต service_role เข้าถึง ai_chat_events
-- ────────────────────────────────────────────────────────────

ALTER TABLE ai_chat_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role full access events" ON ai_chat_events;
CREATE POLICY "Service role full access events" ON ai_chat_events
    FOR ALL USING (auth.role() = 'service_role');

-- Allow anon read for dashboard (ถ้าต้องการให้ dashboard อ่านได้โดยไม่ต้อง login)
-- ⚠️ เปิดเฉพาะถ้าใช้ anon key บน dashboard — ปิดถ้าต้องการ private
DROP POLICY IF EXISTS "Anon read events" ON ai_chat_events;
CREATE POLICY "Anon read events" ON ai_chat_events
    FOR SELECT USING (true);

DROP POLICY IF EXISTS "Anon read leads" ON leads;
CREATE POLICY "Anon read leads" ON leads
    FOR SELECT USING (true);

DROP POLICY IF EXISTS "Anon read customers" ON customers;
CREATE POLICY "Anon read customers" ON customers
    FOR SELECT USING (true);

DROP POLICY IF EXISTS "Anon read ad_attributions" ON ad_attributions;
CREATE POLICY "Anon read ad_attributions" ON ad_attributions
    FOR SELECT USING (true);

-- ────────────────────────────────────────────────────────────
-- 4. ตรวจสอบหลัง migration
-- ────────────────────────────────────────────────────────────
-- SELECT column_name, data_type FROM information_schema.columns
-- WHERE table_name = 'leads' ORDER BY ordinal_position;

-- SELECT table_name FROM information_schema.tables
-- WHERE table_schema = 'public' ORDER BY table_name;
-- ควรเห็น: ad_attributions, ai_chat_events, conversations,
--          customers, leads, tour_interests, tours
