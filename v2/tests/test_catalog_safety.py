"""Tests for customer-visible catalog hygiene."""

from v2.lib.catalog_safety import (
    filter_customer_visible_tours,
    is_customer_visible_tour,
)


def _tour(**overrides):
    row = {
        "web_code": "ap232919",
        "tour_code_real": "BT-NRT",
        "name": "Tokyo Value",
        "base_price": 18999,
        "url": "https://www.tourfiremai.com/tour/ap232919",
    }
    row.update(overrides)
    return row


def test_valid_customer_visible_tour_passes():
    assert is_customer_visible_tour(_tour()) is True


def test_rejects_integration_test_web_code():
    assert is_customer_visible_tour(_tour(web_code="ap_itest_lock_a3649c")) is False


def test_rejects_non_tourfiremai_tour_url():
    assert is_customer_visible_tour(_tour(url="https://x")) is False


def test_rejects_short_fixture_name():
    assert is_customer_visible_tour(_tour(name="T")) is False


def test_rejects_unrealistically_low_fixture_price():
    assert is_customer_visible_tour(_tour(base_price=1000)) is False


def test_filter_preserves_only_customer_visible_rows():
    rows = [
        _tour(web_code="ap100001", name="Tokyo Value", base_price=18999),
        _tour(web_code="ap_itest_lock_bad", name="T", base_price=1000, url="https://x"),
        _tour(web_code="ap100002", name="Osaka Value", base_price=20999),
    ]
    out = filter_customer_visible_tours(rows)
    assert [row["web_code"] for row in out] == ["ap100001", "ap100002"]
