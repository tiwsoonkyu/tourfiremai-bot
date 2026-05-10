-- Task #70: Customer Identity + Bot Pause Controls
-- Run in Supabase SQL Editor

-- 1. Add profile columns to customers table
ALTER TABLE customers
  ADD COLUMN IF NOT EXISTS full_name          text,
  ADD COLUMN IF NOT EXISTS first_name         text,
  ADD COLUMN IF NOT EXISTS last_name          text,
  ADD COLUMN IF NOT EXISTS profile_pic        text,
  ADD COLUMN IF NOT EXISTS profile_updated_at timestamptz;

-- 2. Add bot control columns to leads table
ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS human_takeover  boolean DEFAULT false,
  ADD COLUMN IF NOT EXISTS bot_paused_until timestamptz,
  ADD COLUMN IF NOT EXISTS case_id         text;

-- 3. Index for fast lookup
CREATE INDEX IF NOT EXISTS idx_leads_human_takeover ON leads(human_takeover) WHERE human_takeover = true;
CREATE INDEX IF NOT EXISTS idx_leads_case_id        ON leads(case_id)        WHERE case_id IS NOT NULL;

-- Verify
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name IN ('customers','leads')
  AND column_name IN ('full_name','profile_pic','human_takeover','bot_paused_until','case_id')
ORDER BY table_name, column_name;
