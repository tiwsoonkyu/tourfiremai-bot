-- ─────────────────────────────────────────────────────────────────────────────
-- Migration v2: Faimai Full Features
-- รัน 1 ครั้งใน Supabase SQL Editor
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. Source / faimai columns (รอบแรก)
ALTER TABLE tours
  ADD COLUMN IF NOT EXISTS source_type   TEXT    DEFAULT 'normal',
  ADD COLUMN IF NOT EXISTS is_faimai     BOOLEAN DEFAULT false,
  ADD COLUMN IF NOT EXISTS source_url    TEXT,
  ADD COLUMN IF NOT EXISTS discount_text TEXT,
  ADD COLUMN IF NOT EXISTS badge_text    TEXT;

-- 2. Stale-data columns
ALTER TABLE tours
  ADD COLUMN IF NOT EXISTS is_active     BOOLEAN      DEFAULT true,
  ADD COLUMN IF NOT EXISTS last_seen_at  TIMESTAMPTZ;

-- 3. Discount / pricing columns
ALTER TABLE tours
  ADD COLUMN IF NOT EXISTS original_price    INT,
  ADD COLUMN IF NOT EXISTS promo_price       INT,
  ADD COLUMN IF NOT EXISTS discount_amount   INT,
  ADD COLUMN IF NOT EXISTS discount_percent  NUMERIC(5,1),
  ADD COLUMN IF NOT EXISTS promo_badge       TEXT;

-- 4. Fee detail columns
ALTER TABLE tours
  ADD COLUMN IF NOT EXISTS tip_fee                INT,
  ADD COLUMN IF NOT EXISTS visa_fee    