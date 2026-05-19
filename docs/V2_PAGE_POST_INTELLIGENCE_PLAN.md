# V2 Page Post Intelligence + Sold-Out Signal Plan

Status: `PLANNED`
Controller task: `DEV-2026-05-19-006`

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
3. Meta referral/ad/post source attribution wiring.
4. Response writer policy wiring: sold-out post context -> alternative recommendations.
5. Optional scheduled post ingestion from Meta Graph API after permissions are confirmed.
