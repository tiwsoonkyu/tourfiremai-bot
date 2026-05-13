"""
P1: Structured Intent Memory patch for tourfiremai-bot/app.py
Patches applied (all via string replacement):

PATCH 1:  _EMPTY_CTX — 8 new fields: route_preference, airline_preference,
          airline_type_preference, hotel_preference, trip_style, request_type,
          last_search_price_min, last_search_price_max

PATCH 2:  New functions _detect_intent_modifiers(text) and _build_airline_hint(ctx, found)
          inserted before def process_message(

PATCH 3:  In process_message — apply intent modifiers right after normalize_country_typo block.
          Also add _skip_llm_classify logic: if request_type+country_id known, skip decide_action.

PATCH 4:  Country Retention Guard — if missing_field_to_ask=="country" but ctx has country_id,
          override action→search (never ask country twice).

PATCH 5a: fetch_tours_from_db signature — add airline_filter, price_min_floor params.
PATCH 5b: fetch_tours_from_db body — apply airline ilike filter and price floor gte filter.

PATCH 6:  After select_budget_tiers, apply airline filter + fallback, downgrade sort,
          upgrade price floor post-filter. Save last_search_price_min/max.

PATCH 7:  _airline_hint initialized in outer scope, set in search block,
          appended to tour_data before generate_response call.

Commit: ba7dbde85a7ee11c0907514c866c165667eda627
New app.py SHA: 2256c1d8bae048c388cf4426fdf63942577f5c62
Lines: 5530 (was 5277, +253 lines)
"""
