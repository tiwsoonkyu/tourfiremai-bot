"""
Test fixtures for V2 Sprint 1.

Provides in-memory fakes for Redis and Supabase so unit tests can run without
external dependencies. The fakes implement the duck-typed protocols used in
v2.lib.memory and v2.lib.idempotency.
"""

from __future__ import annotations

import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

import pytest


# --- In-memory Redis fake -----------------------------------------------------

class InMemoryRedis:
    def __init__(self):
        self._store: dict[str, str] = {}
        self._expiry: dict[str, float] = {}

    def _evict(self) -> None:
        now = time.time()
        expired = [k for k, exp in self._expiry.items() if exp <= now]
        for k in expired:
            self._store.pop(k, None)
            self._expiry.pop(k, None)

    def set(self, key: str, value: str, *, nx: bool = False, ex: Optional[int] = None) -> bool:
        self._evict()
        if nx and key in self._store:
            return False
        self._store[key] = str(value)
        if ex is not None:
            # ex=0 → expire immediately; positive ex → expire after N seconds
            self._expiry[key] = time.time() + ex
        else:
            self._expiry.pop(key, None)
        return True

    def setex(self, key: str, ttl: int, value: str) -> bool:
        return self.set(key, value, ex=ttl)

    def get(self, key: str) -> Optional[str]:
        self._evict()
        return self._store.get(key)

    def delete(self, key: str) -> int:
        self._evict()
        removed = 1 if key in self._store else 0
        self._store.pop(key, None)
        self._expiry.pop(key, None)
        return removed

    def eval(self, script: str, numkeys: int, *args) -> Any:
        # Minimal Lua-CAD emulation for the only script we use
        if numkeys == 1 and len(args) == 2:
            key = args[0]
            expected = args[1]
            self._evict()
            if self._store.get(key) == expected:
                self._store.pop(key, None)
                self._expiry.pop(key, None)
                return 1
            return 0
        raise NotImplementedError("Only compare-and-delete Lua emulated")

    def flushall(self) -> None:
        self._store.clear()
        self._expiry.clear()


# --- In-memory Supabase fake --------------------------------------------------

class _InMemoryTable:
    def __init__(self, name: str, store: dict):
        self.name = name
        self._store = store  # store[name] = list[dict]
        self._store.setdefault(name, [])

    @property
    def _rows(self) -> list[dict]:
        return self._store[self.name]

    def _matches(self, row: dict, where: dict) -> bool:
        for k, v in where.items():
            if row.get(k) != v:
                return False
        return True

    def select_one(self, where: dict) -> Optional[dict]:
        for row in self._rows:
            if self._matches(row, where):
                return deepcopy(row)
        return None

    def select_all(self, where: dict) -> list[dict]:
        return [deepcopy(r) for r in self._rows if self._matches(r, where)]

    def select_latest(self, where: dict, order_by: str) -> Optional[dict]:
        matches = [r for r in self._rows if self._matches(r, where)]
        if not matches:
            return None
        matches.sort(key=lambda r: r.get(order_by) or "", reverse=True)
        return deepcopy(matches[0])

    def insert(self, row: dict) -> dict:
        if "id" not in row or row["id"] is None:
            row = {**row, "id": str(uuid.uuid4())}
        if "created_at" not in row:
            row = {**row, "created_at": datetime.now(timezone.utc).isoformat()}
        # enforce uniqueness for known constraints
        if self.name == "selected_tours":
            psid = row.get("psid")
            for r in self._rows:
                if r.get("psid") == psid and r.get("unlocked_at") is None:
                    raise ValueError(
                        f"unique violation: selected_tours active row already exists for psid={psid!r}"
                    )
        self._rows.append(deepcopy(row))
        return deepcopy(row)

    def update(self, where: dict, patch: dict) -> int:
        count = 0
        for row in self._rows:
            if self._matches(row, where):
                # Substitute SQL literal "now()" with actual timestamp
                patch_real = {k: (datetime.now(timezone.utc).isoformat() if v == "now()" else v) for k, v in patch.items()}
                row.update(patch_real)
                count += 1
        return count

    class _CursorStub:
        """Minimal psycopg-cursor compat for code paths that call _cursor()."""
        def __init__(self, table): self._table = table
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, vals=None):
            self._result = None
            # Recognize the MAX(turn_number) query used in webhook handler
            if "MAX(turn_number)" in sql and vals:
                conv_id = vals[0]
                turns = [r for r in self._table._rows if r.get("conversation_id") == conv_id]
                max_n = max((t.get("turn_number") or 0) for t in turns) if turns else 0
                self._result = (max_n,)
        def fetchone(self): return self._result

    def _cursor(self):
        return self._CursorStub(self)

    def upsert(self, *, match: dict, insert: dict, update: dict) -> dict:
        existing = self.select_one(match)
        if existing:
            self.update(match, update)
            return self.select_one(match)
        # Merge match into insert
        merged = {**match, **insert}
        return self.insert(merged)


class InMemorySupabase:
    def __init__(self):
        self._store: dict[str, list[dict]] = {}

    def table(self, name: str) -> _InMemoryTable:
        return _InMemoryTable(name, self._store)

    def reset(self) -> None:
        self._store.clear()


# --- Pytest fixtures ----------------------------------------------------------

@pytest.fixture
def redis() -> InMemoryRedis:
    return InMemoryRedis()


@pytest.fixture
def supabase() -> InMemorySupabase:
    return InMemorySupabase()


@pytest.fixture
def memory_service(supabase, redis):
    from v2.lib.memory import MemoryService
    return MemoryService(supabase, redis)


@pytest.fixture
def memory_service_no_redis(supabase):
    from v2.lib.memory import MemoryService
    return MemoryService(supabase, redis=None)


@pytest.fixture
def make_customer(supabase):
    def _make(psid: str, name: str = "Test Customer") -> str:
        row = supabase.table("customers").insert({"psid": psid, "fb_name": name})
        return row["id"]
    return _make


@pytest.fixture
def make_tour(supabase):
    counter = {"n": 0}
    def _make(*, web_code: str, name: str, price: int, days: int = 5,
              airline: Optional[str] = None, tour_code_real: Optional[str] = None,
              country: str = "ญี่ปุ่น", country_id: int = 2,
              city_tags: Optional[list[str]] = None,
              is_fire_sale: bool = False) -> dict:
        counter["n"] += 1
        row = supabase.table("tours_canonical").insert({
            "web_code": web_code,
            "tour_code_real": tour_code_real,
            "name": name,
            "country": country,
            "country_id": country_id,
            "days": days,
            "nights": max(days - 1, 0),
            "base_price": price,
            "airline": airline,
            "url": f"https://www.tourfiremai.com/tour/{web_code}",
            "city_tags": city_tags or [],
            "is_active": True,
            "is_fire_sale": is_fire_sale,
        })
        return row
    return _make
