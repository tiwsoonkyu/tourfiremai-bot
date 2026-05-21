"""Customer-visible tour catalog hygiene.

This module is deliberately deterministic: staging/test rows must never reach
customer-facing replies even if they exist in the database.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

WEB_CODE_RE = re.compile(r"^ap\d+$", re.I)
TOUR_URL_PREFIX = "https://www.tourfiremai.com/"
MIN_CUSTOMER_VISIBLE_PRICE = 3000
MIN_CUSTOMER_VISIBLE_NAME_LEN = 4


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_customer_visible_tour(row: Mapping[str, Any]) -> bool:
    """Return True only for rows safe to show to customers."""
    web_code = str(row.get("web_code") or "").strip()
    if not WEB_CODE_RE.fullmatch(web_code):
        return False

    name = str(row.get("name") or "").strip()
    if len(name) < MIN_CUSTOMER_VISIBLE_NAME_LEN:
        return False

    url = str(row.get("url") or "").strip()
    if not url.startswith(TOUR_URL_PREFIX):
        return False

    price_raw = row.get("base_price")
    if price_raw is None:
        price_raw = row.get("price")
    price = _as_int(price_raw)
    if price is None or price < MIN_CUSTOMER_VISIBLE_PRICE:
        return False

    days_raw = row.get("days")
    if days_raw is not None:
        days = _as_int(days_raw)
        if days is None or days <= 0:
            return False

    return True


def filter_customer_visible_tours(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Drop fixture, malformed, or non-customer-visible catalog rows."""
    return [
        row for row in rows
        if isinstance(row, Mapping) and is_customer_visible_tour(row)
    ]
