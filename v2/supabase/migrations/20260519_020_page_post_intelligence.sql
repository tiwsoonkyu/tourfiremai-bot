-- Migration 020: Page Post Intelligence + Sold-Out Signal foundation
-- DEV-2026-05-19-006 / docs/V2_PAGE_POST_INTELLIGENCE_PLAN.md
--
-- New tables:
--   - page_posts                    : recent Facebook page posts (3-day rolling memory)
--   - page_post_tour_links          : N:M between page_posts and tours_canonical
--   - tour_availability_overrides   : admin-marked sold_out / full status (per tour,
--                                     departure, or post scope)
--
-- This migration is additive. It does NOT modify any prior table and does NOT
-- change orchestrator or response writer behavior. The deterministic V2 service
-- layer (`v2/lib/page_post_context.py`) consumes these tables. The future
-- Meta Graph API ingester and admin dashboard will write to them.
--
-- Safety:
--   - No live Meta/FB call is required to apply this migration.
--   - service_role only. anon role is denied.
--   - All CHECK constraints prevent obviously-bad rows (e.g. empty status).
--   - tour_availability_overrides intentionally does NOT FK to tours_canonical
--     so admin can pre-mark a "ap123456" sold out even before the canonical
--     row exists (e.g. brand-new fire-sale post). Linkage is by web_code /
--     tour_code_real / tour_id, validated at the application layer.

-- ---------------------------------------------------------------------------
-- page_posts
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS page_posts (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  platform       TEXT NOT NULL DEFAULT 'facebook'
                  CHECK (platform IN ('facebook','instagram','line_oa','website','other')),
  page_id        TEXT NOT NULL,
  post_id        TEXT NOT NULL,
  permalink_url  TEXT,
  posted_at      TIMESTAMPTZ NOT NULL,
  text_hash      TEXT NOT NULL,
  caption_text   TEXT,
  status         TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','archived','removed')),
  active_until   TIMESTAMPTZ,
  source_type    TEXT NOT NULL DEFAULT 'page_post'
                  CHECK (source_type IN ('page_post','ad','organic','unknown')),
  ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  meta           JSONB NOT NULL DEFAULT '{}'::JSONB,

  CONSTRAINT chk_pp_post_id_nonblank CHECK (length(trim(post_id)) > 0),
  CONSTRAINT chk_pp_page_id_nonblank CHECK (length(trim(page_id)) > 0)
);

-- Idempotent upsert key: a Meta post is uniquely identified by (platform, post_id).
CREATE UNIQUE INDEX IF NOT EXISTS uq_page_posts_platform_post
  ON page_posts(platform, post_id);

CREATE INDEX IF NOT EXISTS idx_page_posts_recent
  ON page_posts(posted_at DESC)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_page_posts_active_until
  ON page_posts(active_until)
  WHERE active_until IS NOT NULL;

ALTER TABLE page_posts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS page_posts_anon_no_access ON page_posts;
CREATE POLICY page_posts_anon_no_access ON page_posts FOR ALL TO anon USING (false);
DROP POLICY IF EXISTS page_posts_service_role_all ON page_posts;
CREATE POLICY page_posts_service_role_all ON page_posts FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE  page_posts                   IS 'Recent Facebook/IG/LINE OA page posts (default 3-day relevance window). DEV-2026-05-19-006.';
COMMENT ON COLUMN page_posts.text_hash         IS 'sha256 of caption_text for change-detection on re-ingest.';
COMMENT ON COLUMN page_posts.active_until      IS 'Optional explicit expiry. If NULL, app layer applies the default 3-day window via posted_at + 3d.';
COMMENT ON COLUMN page_posts.source_type       IS 'Source classification for response context: page_post / ad / organic / unknown.';

-- ---------------------------------------------------------------------------
-- page_post_tour_links
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS page_post_tour_links (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  page_post_id    UUID NOT NULL REFERENCES page_posts(id) ON DELETE CASCADE,
  -- One of (web_code, tour_code_real, tour_id) must be set. We do NOT FK
  -- tour_id to tours_canonical because page posts can reference codes that
  -- have not yet been scraped into tours_canonical (e.g. fire-sale).
  web_code        TEXT,
  tour_code_real  TEXT,
  tour_id         UUID,
  confidence      DOUBLE PRECISION NOT NULL DEFAULT 0.5
                   CHECK (confidence BETWEEN 0 AND 1),
  status          TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active','dismissed','superseded')),
  detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  meta            JSONB NOT NULL DEFAULT '{}'::JSONB,

  CONSTRAINT chk_pptl_at_least_one_code CHECK (
    web_code IS NOT NULL OR tour_code_real IS NOT NULL OR tour_id IS NOT NULL
  ),
  CONSTRAINT chk_pptl_codes_differ CHECK (
    web_code IS NULL OR tour_code_real IS NULL OR web_code <> tour_code_real
  )
);

-- Idempotent links: (post, web_code) and (post, tour_code_real) are unique
-- when present. Partial unique indexes keep NULL combinations allowed.
CREATE UNIQUE INDEX IF NOT EXISTS uq_pptl_post_webcode
  ON page_post_tour_links(page_post_id, web_code)
  WHERE web_code IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_pptl_post_realcode
  ON page_post_tour_links(page_post_id, tour_code_real)
  WHERE tour_code_real IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_pptl_post_tour
  ON page_post_tour_links(page_post_id, tour_id)
  WHERE tour_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_pptl_web_code      ON page_post_tour_links(web_code)       WHERE web_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_pptl_tour_code     ON page_post_tour_links(tour_code_real) WHERE tour_code_real IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_pptl_tour_id       ON page_post_tour_links(tour_id)        WHERE tour_id IS NOT NULL;

ALTER TABLE page_post_tour_links ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pptl_anon_no_access ON page_post_tour_links;
CREATE POLICY pptl_anon_no_access ON page_post_tour_links FOR ALL TO anon USING (false);
DROP POLICY IF EXISTS pptl_service_role_all ON page_post_tour_links;
CREATE POLICY pptl_service_role_all ON page_post_tour_links FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE page_post_tour_links IS 'Many-to-many linkage between page_posts and tours_canonical (or pre-canonical codes). DEV-2026-05-19-006.';

-- ---------------------------------------------------------------------------
-- tour_availability_overrides
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tour_availability_overrides (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- Target: at least one of (web_code, tour_code_real, tour_id, page_post_id)
  -- must be set. Combination depends on scope:
  --   scope='tour'      → tour identified by web_code/tour_code_real/tour_id
  --   scope='departure' → tour identified above + departure_date set
  --   scope='post'      → page_post_id set
  web_code        TEXT,
  tour_code_real  TEXT,
  tour_id         UUID,
  page_post_id    UUID REFERENCES page_posts(id) ON DELETE CASCADE,
  departure_date  DATE,

  scope           TEXT NOT NULL CHECK (scope IN ('tour','departure','post')),
  status          TEXT NOT NULL CHECK (status IN ('available','sold_out','full','unknown')),
  reason          TEXT,
  marked_by       TEXT NOT NULL,
  marked_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at      TIMESTAMPTZ,
  cleared_at      TIMESTAMPTZ,
  cleared_by      TEXT,
  meta            JSONB NOT NULL DEFAULT '{}'::JSONB,

  CONSTRAINT chk_tao_codes_differ CHECK (
    web_code IS NULL OR tour_code_real IS NULL OR web_code <> tour_code_real
  ),
  CONSTRAINT chk_tao_target_set CHECK (
    (scope = 'post' AND page_post_id IS NOT NULL)
    OR (scope IN ('tour','departure') AND (
          web_code IS NOT NULL OR tour_code_real IS NOT NULL OR tour_id IS NOT NULL
        ))
  ),
  CONSTRAINT chk_tao_departure_when_scope CHECK (
    (scope <> 'departure') OR (departure_date IS NOT NULL)
  )
);

-- Only one active override is meaningful per (scope, target) tuple. We use
-- partial unique indexes per identifier so admin can have only one active
-- override per tour (or per departure date, or per post) at a time.
CREATE UNIQUE INDEX IF NOT EXISTS uq_tao_active_web_code
  ON tour_availability_overrides(web_code, scope, COALESCE(departure_date, DATE '0001-01-01'))
  WHERE cleared_at IS NULL AND web_code IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_tao_active_real_code
  ON tour_availability_overrides(tour_code_real, scope, COALESCE(departure_date, DATE '0001-01-01'))
  WHERE cleared_at IS NULL AND tour_code_real IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_tao_active_tour_id
  ON tour_availability_overrides(tour_id, scope, COALESCE(departure_date, DATE '0001-01-01'))
  WHERE cleared_at IS NULL AND tour_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_tao_active_post
  ON tour_availability_overrides(page_post_id)
  WHERE cleared_at IS NULL AND scope = 'post' AND page_post_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tao_active_status
  ON tour_availability_overrides(status)
  WHERE cleared_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_tao_expires_at
  ON tour_availability_overrides(expires_at)
  WHERE cleared_at IS NULL AND expires_at IS NOT NULL;

ALTER TABLE tour_availability_overrides ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tao_anon_no_access ON tour_availability_overrides;
CREATE POLICY tao_anon_no_access ON tour_availability_overrides FOR ALL TO anon USING (false);
DROP POLICY IF EXISTS tao_service_role_all ON tour_availability_overrides;
CREATE POLICY tao_service_role_all ON tour_availability_overrides FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE  tour_availability_overrides            IS 'Admin-marked sold_out / full / unknown overrides. Reset (cleared_at IS NOT NULL) instead of deleting for audit. DEV-2026-05-19-006.';
COMMENT ON COLUMN tour_availability_overrides.scope      IS 'tour=whole tour, departure=specific departure_date, post=specific page_post_id.';
COMMENT ON COLUMN tour_availability_overrides.expires_at IS 'Optional TTL. If set and in the past, app layer treats override as cleared.';
