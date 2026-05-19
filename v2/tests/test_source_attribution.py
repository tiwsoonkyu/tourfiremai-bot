"""
v2/tests/test_source_attribution.py — DEV-2026-05-19-008.

Tests for `v2.lib.source_attribution.extract_source` — the deterministic
source-attribution layer that maps a Messenger / IG / LINE webhook event
into a SourceAttribution suitable for `Orchestrator.handle_turn`.

Coverage:

    1. page_post — validated against page_posts table.
    2. ad        — explicit Ads attribution (CTM / IG_CTM / postback AD:).
    3. organic   — no post/ad signal but a real messaging event.
    4. unknown   — empty / junk event.
    5. Attacker-controlled long ref ids are refused.
    6. Unverified `post_id` is dropped at the boundary
       (`to_orchestrator_kwargs()` returns `source_post_id=None`).
    7. Platform inference (`facebook` / `instagram` / `line`).

All tests use the in-memory Supabase fake (`v2/tests/conftest.py`).
"""

from __future__ import annotations

import pytest

from v2.lib.page_post_context import upsert_page_post
from v2.lib.source_attribution import (
    SourceAttribution,
    extract_source,
)


PAGE_ID = "61500000000001"


def _seed_known_post(supabase, post_id: str = "fb_post_known",
                      caption: str = "ทัวร์ไฟไหม้ ap242455"):
    return upsert_page_post(
        supabase, page_id=PAGE_ID, post_id=post_id,
        caption_text=caption, source_type="page_post",
    )


class TestPageMixedSources:
    def test_unknown_when_no_signal(self, supabase):
        attr = extract_source({}, supabase)
        assert attr.source_type == "unknown"
        assert attr.source_post_id is None
        assert attr.source_platform == "facebook"
        kwargs = attr.to_orchestrator_kwargs()
        assert kwargs["source_post_id"] is None
        assert kwargs["source_type"] == "unknown"

    def test_unknown_when_event_is_not_dict(self, supabase):
        for bad in (None, "stringy", 123, [], (1, 2)):
            attr = extract_source(bad, supabase)
            assert attr.source_type == "unknown"
            assert attr.source_post_id is None

    def test_organic_when_message_but_no_post_or_ad(self, supabase):
        event = {
            "sender": {"id": "PSID_X"},
            "message": {"mid": "m_1", "text": "สวัสดีค่ะ ขอสอบถามทัวร์ญี่ปุ่น"},
        }
        attr = extract_source(event, supabase)
        assert attr.source_type == "organic"
        assert attr.source_post_id is None
        assert attr.page_post_validated is False

    def test_ad_signal_via_referral_source(self, supabase):
        event = {
            "sender": {"id": "PSID_X"},
            "message": {"mid": "m_2", "text": "ขอข้อมูลทัวร์ที่เห็นในโฆษณา"},
            "referral": {"source": "ADS", "ad_id": "1234567890"},
        }
        attr = extract_source(event, supabase)
        assert attr.source_type == "ad"
        assert attr.raw_ref == "1234567890"

    def test_ad_signal_via_postback_payload(self, supabase):
        event = {
            "sender": {"id": "PSID_Y"},
            "postback": {"title": "Learn more", "payload": "AD:ad_campaign_42"},
        }
        attr = extract_source(event, supabase)
        assert attr.source_type == "ad"
        assert attr.raw_ref == "ad_campaign_42"

    def test_page_post_validated_against_known_row(self, supabase):
        _seed_known_post(supabase, post_id="fb_post_known")
        event = {
            "sender": {"id": "PSID_Z"},
            "message": {"mid": "m_3", "text": "สนใจโพสต์นี้ค่ะ"},
            "referral": {"ref": "POST:fb_post_known", "source": "SHORTLINK"},
        }
        attr = extract_source(event, supabase)
        assert attr.source_type == "page_post"
        assert attr.source_post_id == "fb_post_known"
        assert attr.page_post_validated is True
        assert attr.page_post_id  # populated with internal uuid

    def test_unverified_post_id_downgraded_to_unknown(self, supabase):
        # No row in page_posts → the candidate must NOT be trusted.
        event = {
            "sender": {"id": "PSID_F"},
            "message": {"mid": "m_4", "text": "ตอบโพสต์ค่ะ"},
            "referral": {"ref": "POST:not_in_db"},
        }
        attr = extract_source(event, supabase)
        assert attr.source_type == "organic"  # message+referral → organic
        assert attr.page_post_validated is False
        kwargs = attr.to_orchestrator_kwargs()
        # Unverified id MUST NOT leak through to the orchestrator.
        assert kwargs["source_post_id"] is None

    def test_reply_to_story_id_is_recognised(self, supabase):
        upsert_page_post(
            supabase, platform="instagram",
            page_id=PAGE_ID, post_id="ig_story_known",
            caption_text="ig caption", source_type="page_post",
        )
        event = {
            "platform": "instagram",
            "sender": {"id": "PSID_IG"},
            "message": {
                "mid": "m_5",
                "reply_to": {"story": {"id": "ig_story_known"}},
                "text": "ตอบสตอรี่ค่ะ",
            },
        }
        attr = extract_source(event, supabase)
        assert attr.source_type == "page_post"
        assert attr.source_platform == "instagram"
        assert attr.source_post_id == "ig_story_known"

    def test_explicit_source_type_wins_when_valid(self, supabase):
        _seed_known_post(supabase, post_id="fb_post_known")
        event = {
            "sender": {"id": "PSID_E"},
            "message": {"mid": "m_6", "text": "ดูจากโฆษณา"},
            "source_type": "ad",
            # No referral/post → ad still applies because of explicit flag.
        }
        attr = extract_source(event, supabase)
        assert attr.source_type == "ad"

    def test_explicit_page_post_without_validation_downgraded(self, supabase):
        # Explicit page_post claim but no row → must downgrade to unknown.
        event = {
            "sender": {"id": "PSID_E2"},
            "message": {"mid": "m_7", "text": "ตอบโพสต์"},
            "source_type": "page_post",
            "source_post_id": "no_such_post",
        }
        attr = extract_source(event, supabase)
        assert attr.source_type == "unknown"
        assert attr.to_orchestrator_kwargs()["source_post_id"] is None

    def test_caller_provided_top_level_source_post_id(self, supabase):
        _seed_known_post(supabase, post_id="fb_post_known")
        event = {
            "sender": {"id": "PSID_T"},
            "message": {"mid": "m_8", "text": "..."},
            "source_post_id": "fb_post_known",
        }
        attr = extract_source(event, supabase)
        assert attr.source_type == "page_post"
        assert attr.source_post_id == "fb_post_known"
        assert attr.page_post_validated is True


class TestSafetyAndPlatform:
    def test_oversized_ref_id_is_refused(self, supabase):
        big = "x" * 1000
        event = {
            "sender": {"id": "PSID"},
            "message": {"mid": "x"},
            "source_post_id": big,
        }
        attr = extract_source(event, supabase)
        assert attr.source_post_id is None
        assert attr.source_type in ("organic", "unknown")

    def test_whitespace_in_ref_id_is_refused(self, supabase):
        event = {
            "sender": {"id": "PSID"},
            "message": {"mid": "x"},
            "source_post_id": "post\nid_with_newline",
        }
        attr = extract_source(event, supabase)
        assert attr.source_post_id is None

    def test_platform_line_inference(self, supabase):
        event = {
            "object": "line",
            "sender": {"id": "U_line_user"},
            "message": {"text": "สนใจทัวร์ค่ะ"},
        }
        attr = extract_source(event, supabase)
        assert attr.source_platform == "line"

    def test_platform_instagram_inference(self, supabase):
        event = {
            "object": "instagram",
            "sender": {"id": "ig_user"},
            "message": {"text": "..."},
        }
        attr = extract_source(event, supabase)
        assert attr.source_platform == "instagram"

    def test_orchestrator_kwargs_drops_unverified(self, supabase):
        attr = SourceAttribution(
            source_type="page_post",
            source_post_id="not_validated",
            source_platform="facebook",
            page_post_validated=False,
        )
        kwargs = attr.to_orchestrator_kwargs()
        assert kwargs["source_post_id"] is None
        assert kwargs["source_type"] == "page_post"  # type itself preserved
        assert kwargs["source_platform"] == "facebook"

    def test_orchestrator_kwargs_passes_validated(self, supabase):
        attr = SourceAttribution(
            source_type="page_post",
            source_post_id="real_id",
            source_platform="facebook",
            page_post_validated=True,
        )
        kwargs = attr.to_orchestrator_kwargs()
        assert kwargs == {
            "source_type": "page_post",
            "source_platform": "facebook",
            "source_post_id": "real_id",
        }
