-- ─────────────────────────────────────────────────────────────────────────────
-- Migration: แยก Faimai / Normal Tours
-- รัน 1 ครั้งใน Supabase SQL Editor
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. เพิ่ม columns ใหม่ในตาราง tours
ALTER TABLE tours
  ADD COLUMN IF NOT EXISTS source_type   TEXT    DEFAULT 'normal',
  ADD COLUMN IF NOT EXISTS is_faimai     BOOLEAN DEFAULT false,
  ADD COLUMN IF NOT EXISTS source_url    TEXT,
  ADD COLUMN IF NOT EXISTS discount_text TEXT,
  ADD COLUMN IF NOT EXISTS badge_text    TEXT;

-- 2. Index เพื่อให้ query เร็ว (is_faimai=true / source_type)
CREATE INDEX IF NOT EXISTS tours_source_type_idx ON tours(source_type);
CREATE INDEX IF NOT EXISTS tours_is_faimai_idx   ON tours(is_faimai);

-- 3. ตั้ง default ให้ rows เดิมทั้งหมดเป็น normal
UPDATE tours
SET source_type = 'normal',
    is_faimai   = false
WHERE source_type IS NULL OR is_faimai IS NULL;

-- 4. (Optional) ดู rows ที่มีอยู่ให้แน่ใจ
-- SELECT source_type, is_faimai, COUNT(*) FROM tours GROUP BY 1,2;
