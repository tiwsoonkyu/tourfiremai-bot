"""
Sprint 2 integration tests against V2 staging Supabase.

Skipped automatically unless V2_STAGING_DB_PASSWORD (or full V2_STAGING_*) env
is set. These tests use REAL Postgres + REAL connection — they are intentionally
slower and isolated from unit tests.

Run with:
    V2_STAGING_DB_PASSWORD='...' \
    V2_STAGING_DB_HOST='aws-1-ap-southeast-1.pooler.supabase.com' \
    V2_STAGING_DB_USER='postgres.mbcihtcdwfofagkxphcu' \
    V2_STAGING_SUPABASE_URL='https://mbcihtcdwfofagkxphcu.supabase.co' \
    V2_STAGING_FB_APP_SECRET='dummy' \
    PYTHONPATH=. pytest v2/tests/test_integration_staging.py -v
"""

import os
import uuid
import pytest

REQUIRED = ("V2_STAGING_DB_PASSWORD", "V2_STAGING_DB_HOST", "V2_STAGING_DB_USER")
all_set = all(os.environ.get(k) for k in REQUIRED)

pytestmark = pytest.mark.skipif(
    not all_set,
    reason=f"Set {REQUIRED} to run integration tests"
)


@pytest.fixture(scope="module")
def staging_supabase():
    from v2.lib.config import load_config
    from v2.lib.db import make_supabase_from_config

    # Fill in minimum env to load_config succeed
    os.environ.setdefault("V2_STAGING_SUPABASE_URL", "https://staging.supabase.co")
    os.environ.setdefault("V2_STAGING_FB_APP_SECRET", "dummy-for-integration")

    cfg = load_config(strict=False)
    return make_supabase_from_config(cfg)


class TestSchemaLive:
    def test_can_insert_customer_and_round_trip(self, staging_supabase):
        psid = f"itest_{uuid.uuid4().hex[:10]}"
        try:
            row = staging_supabase.table("customers").insert({"psid": psid, "fb_name": "Integration Test"})
            assert row["psid"] == psid
            back = staging_supabase.table("customers").select_one({"psid": psid})
            assert back["fb_name"] == "Integration Test"
        finally:
            # Cleanup
            staging_supabase.table("customers").update({"psid": psid}, {"fb_name": "DELETED"})

    def test_check_constraint_rejects_codes_equal(self, staging_supabase):
        """tours_canonical CHECK chk_codes_differ: tour_code_real != web_code."""
        wc = f"ap_itest_{uuid.uuid4().hex[:6]}"
        with pytest.raises(Exception) as excinfo:
            staging_supabase.table("tours_canonical").insert({
                "web_code": wc,
                "tour_code_real": wc,  # same! should reject
                "name": "X",
                "country": "ญี่ปุ่น",
                "country_id": 2,
                "days": 5,
                "nights": 4,
                "base_price": 1000,
                "url": "https://x",
            })
        assert "chk_codes_differ" in str(excinfo.value)

    def test_meta_message_id_dedup(self, staging_supabase):
        """conversation_turns unique partial index on (platform, meta_message_id)."""
        # First create a customer + conversation
        psid = f"itest_dup_{uuid.uuid4().hex[:8]}"
        cust = staging_supabase.table("customers").insert({"psid": psid})
        conv = staging_supabase.table("conversations").insert({
            "customer_id": cust["id"], "psid": psid, "state": "new_lead",
        })
        mid = f"fb:itest_mid_{uuid.uuid4().hex[:8]}"
        row1 = staging_supabase.table("conversation_turns").insert({
            "conversation_id": conv["id"],
            "psid": psid,
            "turn_number": 1,
            "direction": "inbound",
            "speaker": "customer",
            "message_text": "hello",
            "meta_message_id": mid,
            "platform": "fb",
        })
        assert row1 is not None
        # Second insert with same mid should fail
        with pytest.raises(Exception) as excinfo:
            staging_supabase.table("conversation_turns").insert({
                "conversation_id": conv["id"],
                "psid": psid,
                "turn_number": 2,
                "direction": "inbound",
                "speaker": "customer",
                "message_text": "hello again",
                "meta_message_id": mid,
                "platform": "fb",
            })
        assert "idx_turns_dedup" in str(excinfo.value) or "duplicate" in str(excinfo.value).lower()


class TestSelectedTourLock:
    def test_unique_partial_index_active_psid(self, staging_supabase):
        """selected_tours unique partial index ON psid WHERE unlocked_at IS NULL."""
        psid = f"itest_lock_{uuid.uuid4().hex[:8]}"
        cust = staging_supabase.table("customers").insert({"psid": psid})
        # Need a tour
        tour = staging_supabase.table("tours_canonical").insert({
            "web_code": f"ap_itest_lock_{uuid.uuid4().hex[:6]}",
            "name": "T", "country": "ญี่ปุ่น", "country_id": 2,
            "days": 5, "nights": 4, "base_price": 1000, "url": "https://x",
        })
        # Need a conversation
        conv = staging_supabase.table("conversations").insert({
            "customer_id": cust["id"], "psid": psid, "state": "tour_selected",
        })
        # First lock OK
        lock = staging_supabase.table("selected_tours").insert({
            "conversation_id": conv["id"],
            "customer_id": cust["id"],
            "psid": psid,
            "tour_id": tour["id"],
        })
        assert lock["psid"] == psid
        # Second active lock for same psid should fail
        with pytest.raises(Exception) as excinfo:
            staging_supabase.table("selected_tours").insert({
                "conversation_id": conv["id"],
                "customer_id": cust["id"],
                "psid": psid,
                "tour_id": tour["id"],
            })
        assert "idx_selected_one_active_per_psid" in str(excinfo.value) or "duplicate" in str(excinfo.value).lower()
