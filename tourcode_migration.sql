-- Migration: แยก web_code / tour_code_real / airline ให้ชัดเจน

-- 0. เพิ่ม updated_at ก่อน (trigger set_updated_at() ต้องการ field นี้)
ALTER TABLE tours ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- 1. เพิ่ม columns ใหม่
ALTER TABLE tours ADD COLUMN IF NOT EXISTS web_code       TEXT;
ALTER TABLE tours ADD COLUMN IF NOT EXISTS tour_code_real TEXT;

-- 2. ย้าย ap... จาก tour_code → web_code (ถ้า tour_code เก็บ ap... อยู่)
UPDATE tours
SET web_code = tour_code
WHERE web_code IS NULL
  AND tour_code LIKE 'ap%';

-- 3. Indexes
CREATE INDEX IF NOT EXISTS tours_web_code_idx       ON tours