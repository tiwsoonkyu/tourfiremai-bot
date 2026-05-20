"""
v2.lib.db — Production psycopg-based Supabase adapter.

Implements the same SupabaseLike protocol as v2.tests.conftest.InMemorySupabase,
so MemoryService can use either backend without code changes.

Connection pooling is delegated to Supabase Pooler (port 6543); we open a
fresh connection per request and rely on the pooler for efficiency. For long-
lived processes, swap to a connection pool (psycopg_pool) later.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

logger = logging.getLogger("v2.db")


def _adapt_sql_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        from psycopg.types.json import Jsonb

        return Jsonb(value)
    return value


def _adapt_sql_values(values: list[Any]) -> list[Any]:
    return [_adapt_sql_value(v) for v in values]


class PgTable:
    def __init__(self, conn_factory, name: str):
        self._conn_factory = conn_factory
        self.name = name

    @contextmanager
    def _cursor(self) -> Iterator:
        conn = self._conn_factory()
        try:
            with conn.cursor() as cur:
                yield cur
        finally:
            conn.close()

    @staticmethod
    def _build_where(where: dict) -> tuple[str, list]:
        parts = []
        vals = []
        for k, v in where.items():
            if v is None:
                parts.append(f'"{k}" IS NULL')
            else:
                parts.append(f'"{k}" = %s')
                vals.append(_adapt_sql_value(v))
        return " AND ".join(parts) if parts else "TRUE", vals

    def _row_to_dict(self, cur, row) -> Optional[dict]:
        if row is None:
            return None
        return dict(zip([d.name for d in cur.description], row))

    def select_one(self, where: dict) -> Optional[dict]:
        clause, vals = self._build_where(where)
        with self._cursor() as cur:
            cur.execute(f'SELECT * FROM "{self.name}" WHERE {clause} LIMIT 1', vals)
            return self._row_to_dict(cur, cur.fetchone())

    def select_latest(self, where: dict, order_by: str) -> Optional[dict]:
        clause, vals = self._build_where(where)
        with self._cursor() as cur:
            cur.execute(
                f'SELECT * FROM "{self.name}" WHERE {clause} ORDER BY "{order_by}" DESC LIMIT 1',
                vals,
            )
            return self._row_to_dict(cur, cur.fetchone())

    def select_all(self, where: dict) -> list[dict]:
        clause, vals = self._build_where(where)
        with self._cursor() as cur:
            cur.execute(f'SELECT * FROM "{self.name}" WHERE {clause}', vals)
            return [self._row_to_dict(cur, row) for row in cur.fetchall()]

    def insert(self, row: dict) -> dict:
        cols = ", ".join(f'"{k}"' for k in row.keys())
        placeholders = ", ".join(["%s"] * len(row))
        with self._cursor() as cur:
            cur.execute(
                f'INSERT INTO "{self.name}" ({cols}) VALUES ({placeholders}) RETURNING *',
                _adapt_sql_values(list(row.values())),
            )
            return self._row_to_dict(cur, cur.fetchone())

    def update(self, where: dict, patch: dict) -> int:
        if not patch:
            return 0
        set_clause = ", ".join(f'"{k}" = %s' for k in patch.keys())
        clause, w_vals = self._build_where(where)
        with self._cursor() as cur:
            cur.execute(
                f'UPDATE "{self.name}" SET {set_clause} WHERE {clause}',
                _adapt_sql_values(list(patch.values())) + w_vals,
            )
            return cur.rowcount

    def upsert(self, *, match: dict, insert: dict, update: dict) -> dict:
        """
        Two-step upsert: select_one(match) → update or insert.
        Postgres ON CONFLICT would be one round-trip, but match might not
        be a unique constraint; safer to do two queries.
        """
        existing = self.select_one(match)
        if existing:
            if update:
                self.update(match, update)
            return self.select_one(match) or existing
        return self.insert({**match, **insert})


class PgSupabase:
    """
    SupabaseLike adapter backed by psycopg. Provides `.table(name)` API.

    Thread-safe (each call opens its own connection via factory). For higher
    throughput, replace conn_factory with psycopg_pool.ConnectionPool.
    """

    def __init__(self, conn_factory):
        """conn_factory: callable returning a fresh psycopg.Connection."""
        self._conn_factory = conn_factory
        self._lock = threading.Lock()

    def table(self, name: str) -> PgTable:
        return PgTable(self._conn_factory, name)


def make_supabase_from_config(config) -> PgSupabase:
    """Construct PgSupabase using v2.lib.config.Config."""
    import psycopg  # lazy import — keep test runs fast when DB not used

    def factory():
        return psycopg.connect(
            host=config.supabase_db_host,
            port=config.supabase_db_port,
            user=config.supabase_db_user,
            password=config.supabase_db_password,
            dbname=config.supabase_db_name,
            sslmode="require",
            autocommit=True,
            connect_timeout=15,
        )

    return PgSupabase(factory)
