# V2 Data Model — TourFireMai Bot

**วันที่:** 2026-05-16
**Status:** Draft v1

---

## 1. Database Strategy

| Layer | Tech | Purpose |
|-------|------|---------|
| Source of Truth | Supabase (PostgreSQL 15) | Persistent — customers, conversations, offers, tours, fees |
| Hot Cache | Redis | Active session state — invalidated on state change |
| Object Storage | Supabase Storage | PDF files, screenshots |

---

## 2. Supabase Schema (DDL)

### 2.1 `customers` — Long-term customer profile

```sql
CREATE TABLE customers (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  psid            TEXT UNIQUE NOT NULL,           -- Facebook Page-Scoped ID
  fb_name         TEXT,                            -- Profile name (if available)
  first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- Preferences (learned over time)
  preferred_country  TEXT,
  preferred_budget   INTEGER,
  preferred_pax      INTEGER,
  preferred_period   TEXT,                         -- e.g. "ต.ค.-ธ.ค."
  preferred_airline  TEXT,

  -- Sales metadata
  total_conversations INTEGER DEFAULT 0,
  total_bookings      INTEGER DEFAULT 0,
  last_booking_at     TIMESTAMPTZ,
  customer_tier       TEXT DEFAULT 'new',         -- new/active/loyal/dormant

  -- Compliance
  pdpa_consent_at     TIMESTAMPTZ,
  pdpa_consent_text   TEXT,

  -- Misc
  notes               TEXT,                       -- แอดมินกรอกเอง
  tags                TEXT[]                      -- ['vip', 'group_leader', ...]
);

CREATE INDEX idx_customers_psid ON customers(psid);
CREATE INDEX idx_customers_last_seen ON customers(last_seen_at DESC);
CREATE INDEX idx_customers_tier ON customers(customer_tier);
```

### 2.2 `conversations` — Per-thread state

```sql
CREATE TABLE conversations (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id     UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  psid            TEXT NOT NULL,                  -- denormalized for quick lookup
  state           TEXT NOT NULL DEFAULT 'new_lead',  -- see V2_STATE_MACHINE
  started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  closed_at       TIMESTAMPTZ,
  close_reason    TEXT,                           -- booked/no_response/declined/handoff

  -- Active context (mutable)
  current_country   TEXT,
  current_budget    INTEGER,
  current_budget_type TEXT,                       -- strict/flexible
  current_pax       INTEGER,
  current_period    TEXT,
  current_offer_id  UUID,                         -- → offer_snapshots.id
  selected_tour_id  UUID,                         -- → tours_canonical.id
  selected_departure_date DATE,

  -- Handoff
  is_human_paused   BOOLEAN DEFAULT FALSE,
  paused_until      TIMESTAMPTZ,
  paused_reason     TEXT
);

CREATE INDEX idx_conv_psid ON conversations(psid);
CREATE INDEX idx_conv_state ON conversations(state);
CREATE INDEX idx_conv_active ON conversations(last_activity_at DESC) WHERE closed_at IS NULL;
CREATE INDEX idx_conv_human_paused ON conversations(is_human_paused) WHERE is_human_paused = TRUE;
```

### 2.3 `conversation_turns` — Every message (append-only)

```sql
CREATE TABLE conversation_turns (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  psid            TEXT NOT NULL,
  turn_number     INTEGER NOT NULL,
  direction       TEXT NOT NULL,                  -- 'inbound' | 'outbound' | 'system'
  speaker         TEXT NOT NULL,                  -- 'customer' | 'bot' | 'admin'
  message_text    TEXT,
  attachments     JSONB,                          -- [{type, url, ...}]
  state_before    TEXT,
  state_after     TEXT,
  intent          JSONB,                          -- result from Intent Classifier
  tool_calls      JSONB,                          -- [{tool, args, result}]
  llm_model       TEXT,
  llm_tokens_in   INTEGER,
  llm_tokens_out  INTEGER,
  latency_ms      INTEGER,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_turns_conv ON conversation_turns(conversation_id, turn_number);
CREATE INDEX idx_turns_psid_time ON conversation_turns(psid, created_at DESC);
CREATE INDEX idx_turns_direction ON conversation_turns(direction);
```

### 2.4 `offer_snapshots` — Every Top N presentation

```sql
CREATE TABLE offer_snapshots (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  psid            TEXT NOT NULL,
  presented_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  context         JSONB NOT NULL,                 -- {country, budget, pax, period}

  -- The actual offer (locked at this moment)
  tour_list       JSONB NOT NULL,
  -- shape:
  -- [
  --   {
  --     "rank": 1,
  --     "tour_id": "uuid",
  --     "web_code": "ap242455",
  --     "tour_code_real": "BCCKG27-HU",
  --     "name": "...",
  --     "price": 25900,
  --     "days": 5,
  --     "airline": "HU",
  --     "departure_dates": ["2026-06-18", "2026-06-25"],
  --     "tier": "value" | "recommended" | "upgrade"
  --   }
  -- ]

  -- Outcome
  was_selected    BOOLEAN DEFAULT FALSE,
  selected_rank   INTEGER,
  selected_tour_id UUID
);

CREATE INDEX idx_offers_psid ON offer_snapshots(psid, presented_at DESC);
CREATE INDEX idx_offers_conv ON offer_snapshots(conversation_id);
```

### 2.5 `tours_canonical` — Canonical tour database

```sql
CREATE TABLE tours_canonical (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  web_code        TEXT UNIQUE NOT NULL,           -- e.g. "ap242455"
  tour_code_real  TEXT,                            -- e.g. "BCCKG27-HU" (จาก PDF)
  name            TEXT NOT NULL,
  country         TEXT NOT NULL,
  country_id      INTEGER NOT NULL,                -- 1-19 mapping
  city_tags       TEXT[],                          -- ['โตเกียว', 'โอซาก้า']
  days            INTEGER NOT NULL,
  nights          INTEGER NOT NULL,
  base_price      INTEGER NOT NULL,                -- ราคาเริ่มต้น
  airline         TEXT,                            -- 'HU' / 'VZ' / 'XJ' / 'TG'
  wholesale       TEXT,                            -- 'GS' / 'TTN' / 'Best' / 'Zego' / ...
  url             TEXT NOT NULL,                   -- direct link to tourfiremai.com
  pdf_url         TEXT,                            -- link to PDF program

  departure_dates JSONB,                           -- [{date, available, price}]

  description     TEXT,
  highlights      TEXT[],

  is_active       BOOLEAN DEFAULT TRUE,
  is_fire_sale    BOOLEAN DEFAULT FALSE,            -- ทัวร์ไฟไหม้ flag

  scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_synced_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_tours_country ON tours_canonical(country_id) WHERE is_active = TRUE;
CREATE INDEX idx_tours_price ON tours_canonical(base_price) WHERE is_active = TRUE;
CREATE INDEX idx_tours_web_code ON tours_canonical(web_code);
CREATE INDEX idx_tours_real_code ON tours_canonical(tour_code_real);
CREATE INDEX idx_tours_fire_sale ON tours_canonical(is_fire_sale) WHERE is_fire_sale = TRUE;
```

### 2.6 `tour_fees` — PDF-extracted fees

```sql
CREATE TABLE tour_fees (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tour_id         UUID NOT NULL REFERENCES tours_canonical(id) ON DELETE CASCADE,
  tour_code_real  TEXT NOT NULL,
  pdf_url         TEXT NOT NULL,
  pdf_hash        TEXT,                            -- SHA256 for change detection

  -- Extracted fees
  tip_amount      INTEGER,                         -- ค่าทิป
  visa_fee        INTEGER,                         -- ค่าวีซ่า
  single_supplement INTEGER,                       -- พักเดี่ยว
  infant_fee      INTEGER,                         -- ทารก
  child_fee_no_bed INTEGER,                        -- เด็กไม่เตียง
  deposit_amount  INTEGER,                         -- ค่ามัดจำ

  -- Other fees as JSON for flexibility
  other_fees      JSONB,                           -- {fuel_surcharge, tax, ...}

  -- Extraction metadata
  extraction_method TEXT NOT NULL,                 -- 'pdfplumber' | 'ocr' | 'llm_vision'
  extraction_confidence REAL,                      -- 0-1
  extraction_errors TEXT[],                        -- if any field failed

  extracted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  manually_verified BOOLEAN DEFAULT FALSE,
  verified_by     TEXT,
  verified_at     TIMESTAMPTZ
);

CREATE UNIQUE INDEX idx_fees_tour ON tour_fees(tour_id);
CREATE INDEX idx_fees_unverified ON tour_fees(manually_verified) WHERE manually_verified = FALSE;
```

### 2.7 `selected_tours` — Locked customer selections

```sql
CREATE TABLE selected_tours (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  customer_id     UUID NOT NULL REFERENCES customers(id),
  psid            TEXT NOT NULL,
  tour_id         UUID NOT NULL REFERENCES tours_canonical(id),
  tour_code_real  TEXT NOT NULL,                   -- denormalized
  selected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  unlocked_at     TIMESTAMPTZ,                     -- when customer changed mind
  unlock_reason   TEXT,                            -- 'changed_mind' / 'unavailable'

  -- Booking progression
  departure_date_chosen DATE,
  pax_confirmed   INTEGER,
  is_fee_acknowledged BOOLEAN DEFAULT FALSE,
  booking_status  TEXT DEFAULT 'considering'       -- considering / handoff / booked / lost
);

CREATE INDEX idx_selected_psid ON selected_tours(psid) WHERE unlocked_at IS NULL;
CREATE INDEX idx_selected_conv ON selected_tours(conversation_id);
```

### 2.8 `handoff_log` — Human takeover events

```sql
CREATE TABLE handoff_log (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id),
  psid            TEXT NOT NULL,
  triggered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  trigger_type    TEXT NOT NULL,                   -- 'attachment' | 'fee_missing' | 'human_request' | 'payment' | 'booking_confirm' | 'low_confidence'
  trigger_detail  JSONB,
  bot_paused_until TIMESTAMPTZ,
  admin_responded_at TIMESTAMPTZ,
  admin_responder TEXT,                            -- ชื่อแอดมิน
  resolution      TEXT,                            -- 'booked' / 'declined' / 'no_response' / 'bot_resumed'
  resolution_at   TIMESTAMPTZ,
  notes           TEXT
);

CREATE INDEX idx_handoff_psid ON handoff_log(psid, triggered_at DESC);
CREATE INDEX idx_handoff_open ON handoff_log(resolution) WHERE resolution IS NULL;
```

### 2.9 `bot_pauses` — Active bot-pause sessions

```sql
CREATE TABLE bot_pauses (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  psid            TEXT NOT NULL,
  conversation_id UUID REFERENCES conversations(id),
  paused_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  pause_until     TIMESTAMPTZ NOT NULL,
  paused_by       TEXT NOT NULL,                   -- 'system' / 'admin' / 'rule'
  reason          TEXT,
  resumed_at      TIMESTAMPTZ,
  resumed_by      TEXT
);

CREATE INDEX idx_pause_active ON bot_pauses(psid) WHERE resumed_at IS NULL;
```

### 2.10 `agent_traces` — Observability (high-level events)

```sql
CREATE TABLE agent_traces (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trace_id        UUID NOT NULL,                   -- groups all events for one turn
  conversation_id UUID,
  psid            TEXT,
  event_type      TEXT NOT NULL,                   -- 'webhook_received' / 'intent_classified' / 'tool_called' / 'llm_response' / 'message_sent' / 'error'
  event_data      JSONB,
  duration_ms     INTEGER,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_traces_trace ON agent_traces(trace_id, created_at);
CREATE INDEX idx_traces_psid ON agent_traces(psid, created_at DESC);
CREATE INDEX idx_traces_errors ON agent_traces(event_type) WHERE event_type = 'error';
```

### 2.11 `agent_runs` — Per-turn agent execution log

```sql
CREATE TABLE agent_runs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID REFERENCES conversations(id),
  psid            TEXT NOT NULL,
  turn_number     INTEGER,
  trace_id        UUID NOT NULL,                   -- joins to agent_traces + tool_calls

  -- Agent context
  agent_name      TEXT NOT NULL,                   -- 'orchestrator' / 'sales' / 'tour_search' / 'memory' / 'handoff'
  state_before    TEXT,
  state_after     TEXT,

  -- LLM call (if any)
  llm_model       TEXT,
  llm_tokens_in   INTEGER,
  llm_tokens_out  INTEGER,
  llm_latency_ms  INTEGER,

  -- Outcome
  decision        TEXT,                            -- e.g. 'present_top_3' / 'lock_tour' / 'handoff' / 'reply_only'
  decision_data   JSONB,
  errors          JSONB,                           -- list of errors if any
  duration_ms     INTEGER,

  started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at     TIMESTAMPTZ
);

CREATE INDEX idx_runs_psid ON agent_runs(psid, started_at DESC);
CREATE INDEX idx_runs_trace ON agent_runs(trace_id);
CREATE INDEX idx_runs_agent ON agent_runs(agent_name, started_at DESC);
CREATE INDEX idx_runs_errors ON agent_runs((errors IS NOT NULL)) WHERE errors IS NOT NULL;
```

### 2.12 `tool_calls` — Every deterministic tool invocation

```sql
CREATE TABLE tool_calls (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_run_id    UUID REFERENCES agent_runs(id) ON DELETE CASCADE,
  trace_id        UUID NOT NULL,
  conversation_id UUID,
  psid            TEXT,

  tool_name       TEXT NOT NULL,                   -- e.g. 'search_tours' / 'lock_selected_tour' / 'get_tour_fees'
  caller          TEXT,                            -- 'orchestrator' / 'llm' / 'rule_engine'
  input           JSONB,                           -- masked args
  output_summary  JSONB,                           -- not full payload (esp. for large results)
  status          TEXT NOT NULL,                   -- 'success' / 'error' / 'timeout'
  error_code      TEXT,                            -- e.g. 'TOUR_NOT_FOUND', 'FEE_INCOMPLETE'
  error_message   TEXT,
  duration_ms     INTEGER,
  cache_hit       BOOLEAN DEFAULT FALSE,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_tools_run ON tool_calls(agent_run_id);
CREATE INDEX idx_tools_psid_time ON tool_calls(psid, created_at DESC);
CREATE INDEX idx_tools_name_status ON tool_calls(tool_name, status);
CREATE INDEX idx_tools_errors ON tool_calls(status) WHERE status != 'success';
```

### 2.13 `customer_memory` — Bot-access snapshot (wide table)

**Purpose:** denormalized view ที่ bot ใช้ทุก turn — ดึง customer + latest conversation context ใน 1 query เร็ว
ไม่ replace `customers` (ที่เก็บ stable profile + PII) — ทำงานคู่กัน

```sql
CREATE TABLE customer_memory (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id              UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  psid                     TEXT UNIQUE NOT NULL,
  customer_name            TEXT,                        -- mirror of customers.fb_name

  -- Latest known preferences (mirrored from conversations active row)
  latest_country           TEXT,
  latest_city              TEXT,
  budget_per_person        INTEGER,
  budget_type              TEXT,                        -- 'strict' | 'flexible'
  travel_month             TEXT,                        -- e.g. 'มิ.ย. 69' or 'ต.ค.-ธ.ค.'
  pax_count                INTEGER,
  airline_preference       TEXT,

  -- Selection state (mirrored from selected_tours active row)
  selected_tour_web_code   TEXT,
  selected_tour_code_real  TEXT,
  latest_offer_set_id      UUID REFERENCES offer_snapshots(id),
  conversation_state       TEXT,                        -- mirror of conversations.state

  -- Update meta
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_by               TEXT,                        -- 'bot' / 'admin' / 'sync_cron'
  last_update_reason       TEXT                         -- diagnostic — what triggered last write
);

CREATE INDEX idx_cmem_customer ON customer_memory(customer_id);
CREATE INDEX idx_cmem_state ON customer_memory(conversation_state);
CREATE INDEX idx_cmem_updated ON customer_memory(updated_at DESC);
```

**Update rules:**
- Bot writes via `update_customer_memory(psid, patch, reason)` tool
- Mirror sync ทุกครั้งที่:
  - `conversations.state` เปลี่ยน → update `conversation_state`
  - `selected_tours` row ถูก insert → update `selected_tour_*`
  - Offer presented → update `latest_offer_set_id`
  - Preferences inferred from message → update `latest_*` fields
- Cron job เช็คทุก 1 ชม: `customer_memory.updated_at` vs `conversations.last_activity_at` — alert ถ้า drift

**ไม่ใช้:**
- `notes` / `tags[]` / PDPA-sensitive PII → อยู่ใน `customers` เท่านั้น (role-based access)
- Free-form extensible keys → ใช้ JSONB column ถ้าจำเป็นในอนาคต (extension)

### 2.14 `conversation_events` — Immutable event log per conversation

แยกจาก `conversation_turns` (ที่เก็บข้อความ) — `conversation_events` เก็บ side effects เช่น state changes, tool calls summary, memory updates

```sql
CREATE TABLE conversation_events (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  psid            TEXT NOT NULL,
  event_type      TEXT NOT NULL,
  -- 'state_change' / 'memory_update' / 'tour_locked' / 'tour_unlocked' / 'offer_presented' /
  -- 'handoff_triggered' / 'bot_paused' / 'bot_resumed' / 'fee_acknowledged' / 'admin_takeover'
  event_data      JSONB,                           -- structured per event_type
  triggered_by    TEXT,                            -- 'bot' / 'customer_message' / 'admin' / 'cron' / 'system'
  related_turn_id UUID REFERENCES conversation_turns(id),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cevents_conv ON conversation_events(conversation_id, created_at);
CREATE INDEX idx_cevents_psid_time ON conversation_events(psid, created_at DESC);
CREATE INDEX idx_cevents_type ON conversation_events(event_type);
```

### 2.15 `qa_test_runs` — Automated test execution log

```sql
CREATE TABLE qa_test_runs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  test_suite      TEXT NOT NULL,                   -- 'unit' / 'integration' / 'e2e' / 'load' / 'security'
  test_name       TEXT NOT NULL,                   -- e.g. 'M-001', 'L-002', 'E-001'
  test_file       TEXT,                            -- pytest path
  status          TEXT NOT NULL,                   -- 'pass' / 'fail' / 'skip' / 'error'
  duration_ms     INTEGER,
  error_message   TEXT,
  stack_trace     TEXT,

  -- Context
  git_commit      TEXT,
  branch          TEXT,
  triggered_by    TEXT,                            -- 'ci' / 'manual' / 'pre_deploy'
  env             TEXT,                            -- 'dev' / 'staging' / 'prod'

  -- Artifacts
  artifacts       JSONB,                           -- e.g. screenshots, log links

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_qa_suite ON qa_test_runs(test_suite, created_at DESC);
CREATE INDEX idx_qa_fail ON qa_test_runs(status, created_at DESC) WHERE status IN ('fail', 'error');
CREATE INDEX idx_qa_commit ON qa_test_runs(git_commit);
```

### 2.16 `dlq_messages` — Dead Letter Queue สำหรับ poison messages

```sql
CREATE TABLE dlq_messages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  platform        TEXT NOT NULL,                   -- 'fb' / 'line' / 'web'
  meta_message_id TEXT NOT NULL,
  psid            TEXT NOT NULL,
  raw_payload     JSONB NOT NULL,
  failure_count   INTEGER NOT NULL,
  last_error      TEXT,
  last_traceback  TEXT,
  first_failed_at TIMESTAMPTZ NOT NULL,
  last_failed_at  TIMESTAMPTZ NOT NULL,
  resolved        BOOLEAN DEFAULT FALSE,
  resolved_at     TIMESTAMPTZ,
  resolution      TEXT,                            -- 'manual_replay' / 'discarded' / 'fixed_in_code'
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_dlq_unresolved ON dlq_messages(resolved) WHERE resolved = FALSE;
CREATE INDEX idx_dlq_psid ON dlq_messages(psid, last_failed_at DESC);
```

### 2.17 Idempotency Schema Additions

ทุก table ที่เกี่ยวกับการ process message ต้องเก็บ `meta_message_id` เพื่อ dedup at DB layer (ดูรายละเอียดเต็มใน `V2_IDEMPOTENCY_SPEC.md`)

```sql
-- conversation_turns
ALTER TABLE conversation_turns ADD COLUMN meta_message_id TEXT;
ALTER TABLE conversation_turns ADD COLUMN platform TEXT NOT NULL DEFAULT 'fb';
CREATE UNIQUE INDEX idx_turns_dedup ON conversation_turns(platform, meta_message_id)
WHERE meta_message_id IS NOT NULL;

-- conversation_events
ALTER TABLE conversation_events ADD COLUMN meta_message_id TEXT;
ALTER TABLE conversation_events ADD COLUMN platform TEXT NOT NULL DEFAULT 'fb';
CREATE UNIQUE INDEX idx_events_dedup ON conversation_events(platform, meta_message_id)
WHERE meta_message_id IS NOT NULL;

-- agent_runs
ALTER TABLE agent_runs ADD COLUMN meta_message_id TEXT;
ALTER TABLE agent_runs ADD COLUMN platform TEXT NOT NULL DEFAULT 'fb';
CREATE INDEX idx_runs_message ON agent_runs(platform, meta_message_id);

-- tool_calls (no change — already has agent_run_id FK)
CREATE INDEX IF NOT EXISTS idx_tools_by_run ON tool_calls(agent_run_id);
```

---

## 3. Redis Keys

| Pattern | Type | TTL | Purpose |
|---------|------|-----|---------|
| `conv:state:{psid}` | string | 1 ชม. | current state cache |
| `conv:ctx:{psid}` | hash | 1 ชม. | active context (country, budget, ...) |
| `offer:latest:{psid}` | json string | 24 ชม. | mirror of latest offer_snapshot |
| `selected:{psid}` | json string | 24 ชม. | mirror of selected_tour |
| `pause:{psid}` | string (timestamp) | until expiry | quick pause check |
| `lock:conversation:{psid}` | string `{pid}:{trace_id}` | **60 วินาที** | per-PSID serialization lock (updated from 30s per QA finding) |
| `idem:{platform}:{meta_message_id}` | string (trace_id) | **24 ชั่วโมง** | idempotency dedup — fast-path; DB unique index = source of truth |
| `retry:{platform}:{meta_message_id}` | counter | 24 ชั่วโมง | retry counter for DLQ promotion (max 3) |
| `rate:{psid}` | counter | 60 วินาที | rate limit (max 30 msg/min per user) |
| `tours:popular:{country_id}` | json | 1 ชม. | cached popular tours per country |

**Cache invalidation rules:**
- เมื่อ insert ใน `conversations` → invalidate `conv:state:{psid}` + `conv:ctx:{psid}`
- เมื่อ insert ใน `offer_snapshots` → set `offer:latest:{psid}` ใหม่
- เมื่อ insert ใน `selected_tours` → set `selected:{psid}` ใหม่
- เมื่อ insert ใน `bot_pauses` → set `pause:{psid}` ใหม่

---

## 4. Row Level Security (RLS) Policies

Service role (Flask) ใช้ service_key — bypass RLS เพราะ trusted server.
แต่ถ้ามี dashboard ที่ใช้ anon key ต้องมี policy:

```sql
-- Customers: anon can read own row only
CREATE POLICY customers_select_own ON customers
  FOR SELECT USING (psid = current_setting('request.jwt.claims', true)::json->>'psid');

-- Conversations: same
-- ... etc.
```

> ⚠️ Phase 1 ใช้ service_role อย่างเดียว — RLS ไม่ใช้ — แต่ต้องเตรียม policy ไว้สำหรับ Phase 2 (dashboard)

---

## 5. Migration Plan

ใช้ Supabase Migration (raw SQL files):

```
supabase/migrations/
  20260516_001_customers.sql
  20260516_002_conversations.sql
  20260516_003_conversation_turns.sql
  20260516_004_offer_snapshots.sql
  20260516_005_tours_canonical.sql
  20260516_006_tour_fees.sql
  20260516_007_selected_tours.sql
  20260516_008_handoff_log.sql
  20260516_009_bot_pauses.sql
  20260516_010_agent_traces.sql
```

**Migration order (FK dependencies):**
1. `customers` (no FK)
2. `tours_canonical` (no FK)
3. `tour_fees` (FK → tours_canonical)
4. `conversations` (FK → customers)
5. `conversation_turns` (FK → conversations)
6. `offer_snapshots` (FK → conversations)
7. `selected_tours` (FK → conversations, customers, tours_canonical)
8. `handoff_log` (FK → conversations)
9. `bot_pauses` (FK → conversations)
10. `agent_traces` (FK → conversations, optional)
11. `agent_runs` (FK → conversations)
12. `tool_calls` (FK → agent_runs)
13. `customer_memory` (FK → customers, offer_snapshots)
14. `conversation_events` (FK → conversations, conversation_turns)
15. `qa_test_runs` (no FK — operational table)
16. `dlq_messages` (no FK — operational table)
17. **Idempotency ALTER TABLEs** (run after #5, #14, #11): add `meta_message_id` + `platform` + unique indexes

**Rollback strategy:**
- เก็บ migration file `_down.sql` คู่กัน
- Supabase point-in-time backup เปิดใช้งาน

---

## 6. Data Retention Policy

| Table | Retention | Reason |
|-------|----------|--------|
| `customers` | ตลอดไป | Long-term CRM data |
| `conversations` | 2 ปี | Audit + analytics |
| `conversation_turns` | 1 ปี | Log size control |
| `offer_snapshots` | 1 ปี | Analytics |
| `tours_canonical` | ตลอดไป (mark inactive) | Reference data |
| `tour_fees` | ตลอดไป | Reference data |
| `selected_tours` | ตลอดไป | Booking history |
| `handoff_log` | 2 ปี | Audit |
| `bot_pauses` | 90 วัน | Operational |
| `agent_traces` | 30 วัน | Debugging only |
| `agent_runs` | 90 วัน | Replay + analytics |
| `tool_calls` | 60 วัน | Tool performance + bug reproduction |
| `customer_memory` | ตลอดไป (mirror — never auto-delete) | Bot fast-access snapshot |
| `conversation_events` | 1 ปี | Audit trail |
| `qa_test_runs` | 1 ปี | Regression history |
| `dlq_messages` | 1 ปี (resolved=TRUE archive ที่ 90 วัน) | Debug + replay capability |

ใช้ Supabase cron job + DELETE statements

---

## 7. Indexing & Performance Notes

- ทุก table มี `psid` index — query patterns ส่วนใหญ่ filter by psid
- `conversations` partial index `WHERE closed_at IS NULL` — เร็วสำหรับ active conv
- `agent_traces` ใหญ่เร็ว — ต้อง partition by month ถ้า > 100M rows
- `offer_snapshots.tour_list` เป็น JSONB — ถ้า query เข้า field บ่อย ต้องสร้าง GIN index

---

## 7.b. Page Post Intelligence (Sprint 4 follow-up — DEV-2026-05-19-006)

Added in migration `20260519_020_page_post_intelligence.sql`. See
`docs/V2_PAGE_POST_INTELLIGENCE_PLAN.md` for the full contract.

### `page_posts` — recent FB/IG/LINE OA posts (3-day rolling memory)

```sql
CREATE TABLE page_posts (
  id             UUID PRIMARY KEY,
  platform       TEXT NOT NULL DEFAULT 'facebook'
                  CHECK (platform IN ('facebook','instagram','line_oa','website','other')),
  page_id        TEXT NOT NULL,
  post_id        TEXT NOT NULL,
  permalink_url  TEXT,
  posted_at      TIMESTAMPTZ NOT NULL,
  text_hash      TEXT NOT NULL,         -- sha256(caption_text)
  caption_text   TEXT,
  status         TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','archived','removed')),
  active_until   TIMESTAMPTZ,           -- overrides default 3-day window
  source_type    TEXT NOT NULL DEFAULT 'page_post'
                  CHECK (source_type IN ('page_post','ad','organic','unknown')),
  ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  meta           JSONB NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX uq_page_posts_platform_post ON page_posts(platform, post_id);
```

### `page_post_tour_links` — N:M post ↔ tour

```sql
CREATE TABLE page_post_tour_links (
  id              UUID PRIMARY KEY,
  page_post_id    UUID NOT NULL REFERENCES page_posts(id) ON DELETE CASCADE,
  web_code        TEXT,
  tour_code_real  TEXT,
  tour_id         UUID,           -- NOT FK: pre-canonical codes allowed
  confidence      DOUBLE PRECISION NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
  status          TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active','dismissed','superseded')),
  detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  meta            JSONB NOT NULL DEFAULT '{}',
  CONSTRAINT chk_pptl_at_least_one_code CHECK (
    web_code IS NOT NULL OR tour_code_real IS NOT NULL OR tour_id IS NOT NULL
  )
);
```

Partial unique indexes enforce `(page_post_id, web_code)`, `(page_post_id, tour_code_real)`,
and `(page_post_id, tour_id)` idempotency.

### `tour_availability_overrides` — admin sold_out / full overrides

```sql
CREATE TABLE tour_availability_overrides (
  id              UUID PRIMARY KEY,
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
  cleared_at      TIMESTAMPTZ,           -- audit-preserving "soft clear"
  cleared_by      TEXT,
  meta            JSONB NOT NULL DEFAULT '{}'
);
```

Partial unique indexes ensure only one *active* row per (target, scope, departure_date).
Active = `cleared_at IS NULL AND (expires_at IS NULL OR expires_at > now())`.

---

## 8. Data Model Validation Checklist

- [ ] Schema สร้างใน Supabase staging
- [ ] Foreign keys ทุกตัว enforced
- [ ] Indexes สร้างครบ
- [ ] RLS policies เตรียมไว้ (แต่ disabled ใน Phase 1)
- [ ] Migration scripts มี `_down.sql`
- [ ] Sample data insert ทดสอบ JSON parsing
- [ ] Backup snapshot enabled

---

**Next:** Architect Agent review → Tiw approve → Sprint 1 implement
