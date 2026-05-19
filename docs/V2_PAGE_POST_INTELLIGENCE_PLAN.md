# V2 Page Post Intelligence + Sold-Out Signal Plan

Status: `FOUNDATION_IMPLEMENTED` (DEV-2026-05-19-006 — awaiting QA)
Controller task: `DEV-2026-05-19-006`
Implementation: `v2/lib/page_post_context.py`, `v2/supabase/migrations/20260519_020_page_post_intelligence.sql`, `v2/tests/test_page_post_context.py`

## Why This Matters

TourFireMai sales traffic comes from multiple entry points:

- Facebook page posts that admins publish daily
- Paid ads
- Organic inbox messages
- Customers who return later after seeing a post

The AI sales bot should not behave as if every chat starts from zero. If a customer came from a post or references a post, the bot should know what was posted recently, whether the posted tour is still sellable, and whether an admin has marked it full.

## Product Goals

1. Remember page posts for at least the last 3 days.
2. Link recent posts to tour records using web code, real tour code, or tour URL.
3. Let admin mark a posted tour, tour date, or linked post as `sold_out` / `full`.
4. Prevent the bot from recommending sold-out/full options.
5. Give the bot a compact context summary instead of dumping raw post text into the LLM prompt.
6. Support source attribution later: `page_post`, `ad`, `organic`, `unknown`.

## Recommended Architecture

```text
Meta page post / admin input / future dashboard
  -> page_posts
  -> page_post_tour_links
  -> tour_availability_overrides
  -> deterministic tool: get_source_post_context()
  -> deterministic tool: block_if_sold_out()
  -> response writer
```

The LLM should not decide whether a tour is full. The tool/database layer decides, then the LLM only phrases the answer.

## Customer-Facing Behavior

If the post/tour is still available:

> โปรแกรมในโพสต์นี้ยังมีตัวเลือกให้เช็กค่ะ ขอคัดรอบ/ราคาให้ตรงงบก่อนนะคะ

If the post/tour is marked full:

> โปรแกรมในโพสต์นี้เต็มแล้วค่ะ เดี๋ยวช่วยคัดตัวใกล้เคียงที่ยังเปิดรับให้นะคะ

If one date is full but other dates exist:

> รอบวันที่ลูกค้าถามเต็มแล้วค่ะ แต่ยังมีรอบใกล้เคียงให้เลือก เดี๋ยวคัดให้ค่ะ

## Admin Dashboard Behavior Later

Dashboard v0 should show:

- Recent posts from last 3 days
- Linked tour code / web code / tour name
- Current override status: available / sold_out / full / unknown
- Button: `Mark full`
- Button: `Clear full`
- Note/reason field
- Last admin who changed status

## This Task Boundary

`DEV-2026-05-19-006` builds the data model and deterministic service layer only.

Out of scope for this task:

- Live Meta Graph API ingestion
- Visual dashboard UI
- Production webhook source-attribution wiring
- Deployment
- V1 / Make.com changes

## Follow-Up Tasks

1. Dashboard read/write API behind admin auth.
2. Dashboard v0 UI for recent posts + full/sold-out buttons.
3. Meta referral/ad/post source attribution wiring (Webhook side).
4. Response writer policy wiring: sold-out post context -> alternative recommendations.
5. Optional scheduled post ingestion from Meta Graph API after permissions are confirmed.
6. Admin LINE command wiring (e.g. `mark_full <web_code> [reason]`, `clear_full <web_code>`,
   `posts` for recent post list).

## Foundation Layer Contract (DEV-2026-05-19-006)

### New Supabase tables (migration `20260519_020_page_post_intelligence.sql`)

| Table | Key columns | Purpose |
|-------|-------------|---------|
| `page_posts` | `(platform, post_id)` unique | Idempotent storage of recent FB/IG/LINE OA posts; default 3-day relevance via `posted_at`, overridable by `active_until`. |
| `page_post_tour_links` | `(page_post_id, web_code | tour_code_real | tour_id)` partial unique | N:M between posts and tours. `tour_id` is NOT FK to `tours_canonical` (pre-canonical codes allowed). |
| `tour_availability_overrides` | partial unique by (target, scope, departure_date) | Admin sold_out / full / unknown overrides. Audit-preserving: `cleared_at` flips instead of deleting. |

All three tables have RLS enabled with `anon` denied and `service_role` full access, matching prior migrations.

### Deterministic service module (`v2/lib/page_post_context.py`)

Pure functions (no env reads, no live network) consumed by future dashboard / LINE adapter / response writer:

| Function | Purpose |
|----------|---------|
| `upsert_page_post(...)` | Idempotent insert/update keyed by `(platform, post_id)`; computes sha256 `text_hash`. |
| `list_recent_page_posts(days=3, ...)` | Returns active posts within the relevance window (newest first), with each post's compact title (<=80 chars), linked codes, and `is_post_blocked` flag. |
| `extract_tour_references(text)` | Parses web codes / real tour codes / URLs. Reuses `v2.lib.tour_codes` normalisation so airline codes are never mis-classified as tour codes. |
| `link_page_post_to_tour(...)` / `link_page_post_from_text(...)` | Idempotent N:M linkage, validated codes. |
| `mark_availability_override(...)` | Admin marks tour / departure / post as sold_out / full / unknown / available. Auto-clears prior active row on same target. |
| `clear_availability_override(...)` | Clears by `override_id` or by (scope, target). |
| `is_candidate_blocked(...)` | Deterministic block decision with precedence `departure > post > tour`. Returns canned Thai reason text — never LLM-generated. |
| `get_source_context(...)` | Compact, LLM-safe summary of where the customer came from (`page_post`, `ad`, `organic`, `unknown`). |
| `build_response_planning_context(...)` | Single planner entrypoint: source summary + block decision + `replacement_needed` flag + safe Thai reason. |

### Hard rules enforced by tests

- No wholesale partner names ever appear in returned strings (`_WHOLESALE_BLACKLIST` reused).
- Caption text is capped to `CONTEXT_TITLE_MAX_CHARS = 80` chars in any surface bundle.
- Bot/admin reason text capped to `CONTEXT_REASON_MAX_CHARS = 160`.
- No live Meta/FB, LINE, OpenAI, OCR, or paid-provider call in tests or in the module.
- LLM never decides sold-out semantics — code does.
