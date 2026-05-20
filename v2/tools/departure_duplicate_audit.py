"""
v2.tools.departure_duplicate_audit — Find logical duplicates in
``tour_departures`` for the (tour_id, departure_start, departure_end,
COALESCE(bus, 0)) key.

Sprint 5 Package I / DEV-2026-05-20-015.

Why this exists
---------------
``idx_dep_full_row`` (created in migration 021) is currently a non-unique
index. Before promoting it to ``UNIQUE`` we need a safe audit to prove
the table has zero duplicates. This module provides:

    - a pure Python helper (``find_duplicates``) the orchestrator /
      operator can run against any ``select_all``-capable supabase
      client (used by tests against InMemorySupabase);
    - a raw SQL block (``DUPLICATE_AUDIT_SQL``) operators can run
      directly against the staging DB before applying any future
      uniqueness migration.

Hard rules
----------
- READ-ONLY. Never deletes, mutates, or upserts. The audit is a yes/no
  signal — fixing duplicates is a separate, manually-approved task.
- No network, no secrets, no env reads.
- ``find_duplicates`` aggregates by the same key the future UNIQUE
  index would use:
      (tour_id, departure_start, departure_end, COALESCE(bus, 0))
- Rows missing ``departure_start`` are excluded from the duplicate
  count — they would also be excluded by a partial UNIQUE index that
  matches the application's "no NULL start dates" invariant.

Public API
----------
    DuplicateGroup                            -- dataclass
    DuplicateAuditResult                      -- dataclass
    find_duplicates(supabase)                 -- entrypoint
    DUPLICATE_AUDIT_SQL                       -- raw SQL string
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("v2.tools.departure_duplicate_audit")


# SQL the operator runs on staging before approving any UNIQUE migration.
# It's a pure read; never writes. The query mirrors the Python aggregation
# below so both surfaces stay in sync.
DUPLICATE_AUDIT_SQL = """
-- Departure uniqueness audit
-- Returns one row per duplicate logical key. Zero rows = safe to add
-- UNIQUE (tour_id, departure_start, departure_end, COALESCE(bus,0))
-- on tour_departures.
SELECT
    tour_id,
    departure_start,
    departure_end,
    COALESCE(bus, 0) AS bus_key,
    COUNT(*) AS dupes
FROM tour_departures
WHERE departure_start IS NOT NULL
GROUP BY tour_id, departure_start, departure_end, COALESCE(bus, 0)
HAVING COUNT(*) > 1
ORDER BY dupes DESC, tour_id, departure_start;
""".strip()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DuplicateGroup:
    """One group of rows that share a logical key.

    ``row_ids`` are the ``id`` column values of every offending row in
    the group. Use them for follow-up triage; never auto-delete.
    """

    tour_id: Optional[str]
    departure_start: Optional[str]
    departure_end: Optional[str]
    bus_key: int
    count: int
    row_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "tour_id": self.tour_id,
            "departure_start": self.departure_start,
            "departure_end": self.departure_end,
            "bus_key": self.bus_key,
            "count": self.count,
            "row_ids": list(self.row_ids),
        }


@dataclass
class DuplicateAuditResult:
    """Summary of an audit pass."""

    total_rows: int = 0
    rows_with_start: int = 0
    duplicate_groups: list[DuplicateGroup] = field(default_factory=list)

    @property
    def safe_for_unique_index(self) -> bool:
        """True only when there are zero offending groups."""
        return not self.duplicate_groups

    def to_dict(self) -> dict:
        return {
            "total_rows": self.total_rows,
            "rows_with_start": self.rows_with_start,
            "duplicate_groups": [g.to_dict() for g in self.duplicate_groups],
            "safe_for_unique_index": self.safe_for_unique_index,
        }


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def _isoish(v: Any) -> Optional[str]:
    if v is None or v == "":
        return None
    if isinstance(v, str):
        return v
    try:
        return v.isoformat()
    except AttributeError:
        return str(v)


def find_duplicates(supabase: Any) -> DuplicateAuditResult:
    """Aggregate ``tour_departures`` by the future UNIQUE-key tuple and
    surface every group with ``count > 1``.

    Pure read; never mutates the DB. Skips rows whose
    ``departure_start`` is NULL — those would also be excluded by a
    partial UNIQUE index that mirrors the application invariant of
    "no NULL start dates".
    """
    try:
        rows = supabase.table("tour_departures").select_all({}) or []
    except Exception as e:
        logger.warning("find_duplicates select_all failed: %s", e)
        return DuplicateAuditResult()

    result = DuplicateAuditResult(total_rows=len(rows))
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        start = r.get("departure_start")
        if start in (None, ""):
            continue
        result.rows_with_start += 1
        key = (
            r.get("tour_id"),
            _isoish(start),
            _isoish(r.get("departure_end")),
            int(r.get("bus") or 0),
        )
        groups.setdefault(key, []).append(r)

    for key, members in groups.items():
        if len(members) <= 1:
            continue
        result.duplicate_groups.append(
            DuplicateGroup(
                tour_id=key[0],
                departure_start=key[1],
                departure_end=key[2],
                bus_key=key[3],
                count=len(members),
                row_ids=[str(m.get("id")) for m in members if m.get("id")],
            )
        )
    # Stable ordering — largest groups first, then by start date.
    result.duplicate_groups.sort(
        key=lambda g: (-g.count, g.departure_start or "")
    )
    return result
