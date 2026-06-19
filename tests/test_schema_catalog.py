"""Catalog SQL builders, reader bounding/overflow, and TSV shaping + truncation."""

import contextlib

from infra_mcp import db
from infra_mcp.config import DatabaseConfig
from infra_mcp.tools import db_tools


def _dbcfg() -> DatabaseConfig:
    return DatabaseConfig(name="d", db_name="app", user="ro", password="p")


class _FakeCursor:
    def __init__(self, script):
        self.script = list(script)
        self.executed = []
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._rows = self.script.pop(0)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0]


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def rollback(self):
        pass

    def close(self):
        pass


def _patch_session(monkeypatch, script):
    cur = _FakeCursor(script)

    @contextlib.contextmanager
    def fake(db_cfg):
        yield _FakeConn(cur)

    monkeypatch.setattr(db, "_session", fake)
    monkeypatch.setattr(db.audit, "log_command", lambda *a, **k: None)
    return cur


# --- SQL builders -----------------------------------------------------------


def test_list_tables_sql_shape():
    sql = db._list_tables_sql(201)
    assert sql.lstrip().upper().startswith("SELECT")
    assert "information_schema.tables" in sql
    assert "BASE TABLE" in sql
    assert "NOT IN ('pg_catalog', 'information_schema')" in sql
    assert "ORDER BY table_schema, table_name" in sql
    assert "LIMIT 201" in sql
    db.guard_select(sql)  # internal SQL must pass the SELECT guard


def test_columns_sql_uses_bound_params_and_order():
    sql = db._columns_sql(201)
    assert "information_schema.columns" in sql
    assert "table_schema = %s AND table_name = %s" in sql
    assert "ORDER BY ordinal_position" in sql
    assert "LIMIT 201" in sql
    db.guard_select(sql)


def test_pk_and_fk_builders_pass_guard():
    for sql in (db._primary_key_sql(), db._foreign_keys_sql()):
        assert "tc.table_schema = %s AND tc.table_name = %s" in sql
        db.guard_select(sql)
    assert "PRIMARY KEY" in db._primary_key_sql()
    assert "FOREIGN KEY" in db._foreign_keys_sql()


# --- list_tables reader -----------------------------------------------------


def test_list_tables_not_truncated_skips_count(monkeypatch):
    rows = [("public", "orders"), ("public", "customers")]
    cur = _patch_session(monkeypatch, [rows])
    result = db.list_tables(_dbcfg(), audit_log_path=None)
    assert result.truncated is False
    assert result.total == 2
    assert [(t.schema, t.name) for t in result.tables] == rows
    assert len(cur.executed) == 1  # no follow-up count(*) on the non-overflow path


def test_list_tables_truncated_runs_count(monkeypatch):
    rows = [("public", f"t_{i:04d}") for i in range(201)]  # cap+1 → overflow
    cur = _patch_session(monkeypatch, [rows, [(412,)]])
    result = db.list_tables(_dbcfg(), audit_log_path=None)
    assert result.truncated is True
    assert result.total == 412
    assert len(result.tables) == 200
    assert len(cur.executed) == 2  # list + exact count(*)


# --- describe_table reader --------------------------------------------------


def test_describe_table_truncated_columns(monkeypatch):
    cols = [(f"col_{i:04d}", "integer") for i in range(201)]
    _patch_session(
        monkeypatch,
        [cols, [(380,)], [("col_0001",)], []],  # columns, count, pk, fk
    )
    desc = db.describe_table(_dbcfg(), "public", "wide", audit_log_path=None)
    assert desc.truncated is True
    assert desc.total_columns == 380
    assert len(desc.columns) == 200
    assert desc.primary_key == ["col_0001"]


def test_describe_table_missing_returns_none(monkeypatch):
    _patch_session(monkeypatch, [[]])  # no columns → table does not exist
    assert db.describe_table(_dbcfg(), "public", "nope", audit_log_path=None) is None


def test_describe_table_foreign_keys(monkeypatch):
    cols = [("id", "integer"), ("customer_id", "integer")]
    fks = [("customer_id", "public", "customers", "id")]
    _patch_session(monkeypatch, [cols, [("id",)], fks])
    desc = db.describe_table(_dbcfg(), "public", "orders", audit_log_path=None)
    assert desc.foreign_keys[0].column == "customer_id"
    assert desc.foreign_keys[0].references == "public.customers"
    assert desc.foreign_keys[0].ref_column == "id"


# --- bounding helper + TSV shaping ------------------------------------------


def test_bound_catalog_caps_and_marks():
    rows = list(range(412))
    capped, marker = db.bound_catalog(rows, total=412, unit="tables", cap=200)
    assert len(capped) == 200
    assert marker == "-- TRUNCATED: showing 200 of 412 tables"


def test_bound_catalog_no_marker_when_under_cap():
    capped, marker = db.bound_catalog([1, 2], total=2, unit="tables", cap=200)
    assert marker is None
    assert capped == [1, 2]


def test_render_table_list_tsv_with_truncation():
    entry = {
        "tables": [{"schema": "public", "name": f"t_{i:04d}"} for i in range(200)],
        "truncated": True,
        "total": 412,
    }
    out = db_tools._render_table_list(entry).splitlines()
    assert out[0] == "schema\ttable"
    assert out[1] == "public\tt_0000"
    assert out[-1] == "-- TRUNCATED: showing 200 of 412 tables"


def test_render_table_list_empty():
    out = db_tools._render_table_list({"tables": [], "truncated": False, "total": 0})
    assert out == "schema\ttable\n-- no user tables"


def test_render_description_sections():
    entry = {
        "qualified": "public.orders",
        "columns": [{"name": "id", "type": "integer"}],
        "primary_key": ["id"],
        "foreign_keys": [
            {"column": "customer_id", "references": "public.customers", "ref_column": "id"}
        ],
        "truncated": False,
        "total_columns": 1,
    }
    out = db_tools._render_description(entry)
    assert "table: public.orders" in out
    assert "=== columns ===" in out
    assert "id\tinteger" in out
    assert "=== primary key ===" in out
    assert "=== foreign keys ===" in out
    assert "customer_id\tpublic.customers\tid" in out
