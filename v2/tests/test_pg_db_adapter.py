from __future__ import annotations

from types import SimpleNamespace

from psycopg.types.json import Jsonb

from v2.lib.db import PgTable


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = [SimpleNamespace(name="id"), SimpleNamespace(name="payload")]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, vals=None):
        self.conn.queries.append((sql, vals or []))

    def fetchone(self):
        return ("row-1", {"ok": True})

    def fetchall(self):
        return [("row-1", {"ok": True}), ("row-2", {"ok": False})]

    @property
    def rowcount(self):
        return 1


class FakeConnection:
    def __init__(self):
        self.queries = []
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def _table():
    conn = FakeConnection()
    return PgTable(lambda: conn, "conversation_turns"), conn


def test_select_all_queries_all_rows_and_maps_dicts():
    table, conn = _table()

    rows = table.select_all({})

    assert rows == [
        {"id": "row-1", "payload": {"ok": True}},
        {"id": "row-2", "payload": {"ok": False}},
    ]
    assert conn.queries == [('SELECT * FROM "conversation_turns" WHERE TRUE', [])]
    assert conn.closed is True


def test_select_all_supports_where_and_null_predicates():
    table, conn = _table()

    table.select_all({"psid": "P1", "closed_at": None})

    sql, vals = conn.queries[0]
    assert sql == 'SELECT * FROM "conversation_turns" WHERE "psid" = %s AND "closed_at" IS NULL'
    assert vals == ["P1"]


def test_insert_adapts_dict_and_list_values_to_jsonb():
    table, conn = _table()

    table.insert({"psid": "P1", "intent": {"action": "reply"}, "attachments": ["a.jpg"]})

    _sql, vals = conn.queries[0]
    assert vals[0] == "P1"
    assert isinstance(vals[1], Jsonb)
    assert vals[1].obj == {"action": "reply"}
    assert isinstance(vals[2], Jsonb)
    assert vals[2].obj == ["a.jpg"]


def test_update_adapts_patch_json_values_but_preserves_where_values():
    table, conn = _table()

    table.update({"psid": "P1"}, {"event_data": {"source": "organic"}})

    _sql, vals = conn.queries[0]
    assert isinstance(vals[0], Jsonb)
    assert vals[0].obj == {"source": "organic"}
    assert vals[1] == "P1"
