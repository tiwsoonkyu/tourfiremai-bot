"""Sprint 1 test: memory layer + offer snapshot + selected_tour lock."""

import pytest
from v2.lib.memory import (
    MemoryService,
    TourOption,
    OfferSnapshot,
    CustomerMemoryView,
    resolve_tour_selection,
)


# Helpers ----------------------------------------------------------------------

def _make_options() -> list[TourOption]:
    """Build a stable 3-tour offer for tests."""
    return [
        TourOption(
            rank=1, web_code="ap111111", tour_code_real="BCCKG27-HU",
            name="ทัวร์โตเกียว 5 วัน 4 คืน",
            price=18999, days=5, airline="HU",
            departure_dates=["2026-06-18"], url="https://x/1",
            tier="value", city_tags=["โตเกียว"],
        ),
        TourOption(
            rank=2, web_code="ap222222", tour_code_real="BCCKG28-VZ",
            name="ทัวร์โอซาก้า 6 วัน 5 คืน",
            price=25900, days=6, airline="VZ",
            departure_dates=["2026-07-01"], url="https://x/2",
            tier="recommended", city_tags=["โอซาก้า"],
        ),
        TourOption(
            rank=3, web_code="ap333333", tour_code_real="BCCKG29-XJ",
            name="ทัวร์ฮอกไกโด คิวชู พรีเมียม",
            price=32900, days=7, airline="XJ",
            departure_dates=["2026-09-15"], url="https://x/3",
            tier="upgrade", city_tags=["ฮอกไกโด", "คิวชู"],
        ),
    ]


# Customer Memory --------------------------------------------------------------

class TestCustomerMemory:
    def test_returns_empty_for_unknown_psid(self, memory_service):
        view = memory_service.get_customer_memory("UNKNOWN_PSID")
        assert isinstance(view, CustomerMemoryView)
        assert view.customer_id is None
        assert view.psid == "UNKNOWN_PSID"
        assert view.is_returning_customer is False

    def test_update_creates_customer_and_memory(self, memory_service, supabase):
        result = memory_service.update_customer_memory(
            "PSID_1",
            {"fb_name": "Alice", "latest_country": "ญี่ปุ่น", "budget_per_person": 30000},
            reason="initial_chat",
        )
        assert "fb_name" in result["updated_fields_customers"]
        assert "latest_country" in result["updated_fields_memory"]
        assert "budget_per_person" in result["updated_fields_memory"]

        view = memory_service.get_customer_memory("PSID_1")
        assert view.customer_name == "Alice"
        assert view.latest_country == "ญี่ปุ่น"
        assert view.budget_per_person == 30000

    def test_update_protected_field_blocked(self, memory_service):
        result = memory_service.update_customer_memory(
            "PSID_1", {"notes": "admin note from bot"}, reason="bot_attempt"
        )
        assert ("notes", "protected_field_admin_only") in result["skipped_fields"]

    def test_update_unknown_field_skipped(self, memory_service):
        result = memory_service.update_customer_memory(
            "PSID_1", {"random_unknown_field": "x"}, reason="test"
        )
        assert any(s[0] == "random_unknown_field" for s in result["skipped_fields"])


# Offer Snapshots --------------------------------------------------------------

class TestOfferSnapshot:
    def test_save_and_get_latest(self, memory_service):
        options = _make_options()
        memory_service.update_customer_memory("PSID_1", {"fb_name": "X"}, reason="x")
        snap = memory_service.save_offer_snapshot(
            "PSID_1", options, search_context={"country": "ญี่ปุ่น", "budget": 30000}
        )
        assert isinstance(snap, OfferSnapshot)
        assert snap.psid == "PSID_1"
        assert len(snap.tour_list) == 3

        latest = memory_service.get_latest_offer_snapshot("PSID_1")
        assert latest is not None
        assert latest.id == snap.id
        assert latest.tour_list[0].web_code == "ap111111"

    def test_save_with_empty_options_raises(self, memory_service):
        with pytest.raises(ValueError):
            memory_service.save_offer_snapshot("PSID_1", [], search_context={})

    def test_redis_miss_falls_back_to_supabase(self, memory_service, redis):
        memory_service.update_customer_memory("PSID_1", {"fb_name": "X"}, reason="x")
        snap = memory_service.save_offer_snapshot("PSID_1", _make_options(), {})
        # Flush Redis to simulate restart
        redis.flushall()
        latest = memory_service.get_latest_offer_snapshot("PSID_1")
        assert latest is not None
        assert latest.id == snap.id

    def test_no_redis_works(self, memory_service_no_redis):
        memory_service_no_redis.update_customer_memory("PSID_1", {"fb_name": "X"}, reason="x")
        snap = memory_service_no_redis.save_offer_snapshot("PSID_1", _make_options(), {})
        latest = memory_service_no_redis.get_latest_offer_snapshot("PSID_1")
        assert latest is not None
        assert latest.id == snap.id


# Selected Tour Lock -----------------------------------------------------------

class TestSelectedTourLock:
    def test_lock_then_get(self, memory_service, make_tour):
        memory_service.update_customer_memory("PSID_1", {"fb_name": "X"}, reason="x")
        tour = make_tour(web_code="ap111111", name="ทัวร์โตเกียว", price=18999, airline="HU")
        lock = memory_service.lock_selected_tour("PSID_1", tour)
        assert lock.is_locked
        assert lock.web_code == "ap111111"

        got = memory_service.get_selected_tour("PSID_1")
        assert got is not None
        assert got.tour_id == lock.tour_id

    def test_double_lock_raises(self, memory_service, make_tour):
        memory_service.update_customer_memory("PSID_1", {"fb_name": "X"}, reason="x")
        tour1 = make_tour(web_code="ap111111", name="A", price=18999)
        tour2 = make_tour(web_code="ap222222", name="B", price=25900)
        memory_service.lock_selected_tour("PSID_1", tour1)
        with pytest.raises(ValueError):
            memory_service.lock_selected_tour("PSID_1", tour2)

    def test_clear_then_relock(self, memory_service, make_tour):
        memory_service.update_customer_memory("PSID_1", {"fb_name": "X"}, reason="x")
        tour1 = make_tour(web_code="ap111111", name="A", price=18999)
        tour2 = make_tour(web_code="ap222222", name="B", price=25900)
        memory_service.lock_selected_tour("PSID_1", tour1)
        memory_service.clear_selected_tour("PSID_1", reason="changed_mind")
        # Now relock with tour2
        new_lock = memory_service.lock_selected_tour("PSID_1", tour2)
        assert new_lock.web_code == "ap222222"

    def test_lock_without_customer_raises(self, memory_service, make_tour):
        tour = make_tour(web_code="ap111111", name="A", price=18999)
        with pytest.raises(ValueError):
            memory_service.lock_selected_tour("PSID_GHOST", tour)


# Resolve Tour Selection -------------------------------------------------------

class TestResolveSelection:
    @pytest.fixture
    def snapshot(self) -> OfferSnapshot:
        return OfferSnapshot(
            id="snap_1",
            conversation_id="conv_1",
            psid="PSID_1",
            presented_at="2026-05-17T00:00:00Z",
            context={"country": "ญี่ปุ่น"},
            tour_list=_make_options(),
        )

    def test_resolve_by_index_1(self, snapshot):
        r = resolve_tour_selection("ตัวที่ 1", snapshot)
        assert r.matched
        assert r.match_kind == "index"
        assert r.option.web_code == "ap111111"

    def test_resolve_by_index_2(self, snapshot):
        r = resolve_tour_selection("ตัวที่ 2", snapshot)
        assert r.matched
        assert r.option.rank == 2

    def test_resolve_by_thai_first(self, snapshot):
        r = resolve_tour_selection("เอาตัวแรก", snapshot)
        assert r.matched
        assert r.option.rank == 1

    def test_resolve_by_thai_word(self, snapshot):
        r = resolve_tour_selection("เอาที่สอง", snapshot)
        assert r.matched
        assert r.option.rank == 2

    def test_resolve_by_web_code(self, snapshot):
        r = resolve_tour_selection("ขอดู ap222222 หน่อย", snapshot)
        assert r.matched
        assert r.match_kind == "web_code"
        assert r.option.web_code == "ap222222"

    def test_resolve_by_tour_code_real(self, snapshot):
        r = resolve_tour_selection("รหัส BCCKG27-HU", snapshot)
        assert r.matched
        assert r.match_kind == "tour_code_real"
        assert r.option.tour_code_real == "BCCKG27-HU"

    def test_resolve_by_price_exact(self, snapshot):
        r = resolve_tour_selection("เอาราคา 25,900", snapshot)
        assert r.matched
        assert r.match_kind == "price"
        assert r.option.price == 25900

    def test_resolve_by_price_within_tolerance(self, snapshot):
        r = resolve_tour_selection("18999 ครับ", snapshot)
        assert r.matched
        assert r.option.price == 18999

    def test_duplicate_price_asks_clarify(self):
        # Two tours at same price → needs_clarification
        options = [
            TourOption(rank=1, web_code="ap1", tour_code_real=None,
                       name="A", price=20000, days=5, airline="TG"),
            TourOption(rank=2, web_code="ap2", tour_code_real=None,
                       name="B", price=20000, days=6, airline="VZ"),
        ]
        snap = OfferSnapshot(id="x", conversation_id=None, psid="PSID",
                              presented_at="now", context={}, tour_list=options)
        r = resolve_tour_selection("เอาราคา 20000", snap)
        assert not r.matched
        assert r.needs_clarification
        assert r.clarification_reason == "duplicate_price"
        assert len(r.candidates) == 2

    def test_resolve_by_city_keyword_unique(self, snapshot):
        # คิวชู → only tour #3 has it
        r = resolve_tour_selection("ขอคิวชู", snapshot)
        assert r.matched
        assert r.match_kind == "city"
        assert r.option.rank == 3

    def test_index_out_of_range(self, snapshot):
        r = resolve_tour_selection("ตัวที่ 99", snapshot)
        assert not r.matched
        assert r.needs_clarification
        assert "out_of_range" in r.clarification_reason

    def test_no_match(self, snapshot):
        r = resolve_tour_selection("blah blah random text", snapshot)
        assert not r.matched
        assert r.clarification_reason == "no_match"
