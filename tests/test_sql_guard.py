"""Unit tests for the SQL SELECT guard."""

import contextlib

import pytest

from infra_mcp import db
from infra_mcp.config import DatabaseConfig
from infra_mcp.db import guard_select
from infra_mcp.errors import InfraMcpError, ReadOnlyViolationError


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select * from jobs",
        "  SELECT id FROM users WHERE id = 1",
        "SELECT * FROM pg_stat_activity;",
    ],
)
def test_select_passes(sql):
    guard_select(sql)  # should not raise


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM jobs",
        "UPDATE jobs SET x = 1",
        "INSERT INTO jobs VALUES (1)",
        "DROP TABLE jobs",
        "TRUNCATE jobs",
    ],
)
def test_non_select_rejected(sql):
    with pytest.raises(ReadOnlyViolationError):
        guard_select(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE jobs",
        "SELECT 1; DELETE FROM jobs",
        "SELECT 1; SELECT 2",
    ],
)
def test_multi_statement_rejected(sql):
    with pytest.raises(ReadOnlyViolationError):
        guard_select(sql)


# --- Schema introspection: guard + parameter-bound table name (SC-004, FR-009) ---


def test_introspection_sql_passes_guard():
    """Every internal catalog SELECT must pass the SELECT guard unchanged."""
    for sql in (
        db._list_tables_sql(201),
        db._count_tables_sql(),
        db._resolve_schema_sql(),
        db._columns_sql(201),
        db._count_columns_sql(),
        db._primary_key_sql(),
        db._foreign_keys_sql(),
    ):
        guard_select(sql)  # must not raise


class _SpyCursor:
    def __init__(self):
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return []  # mutation-shaped name matches no real table

    def fetchone(self):
        return (0,)


class _SpyConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def rollback(self):
        pass

    def close(self):
        pass


def test_mutation_shaped_table_resolves_to_not_found(monkeypatch):
    """A mutation-shaped table arg is bound as a %s parameter, so it matches no
    table → 'not found', and ONLY a SELECT is ever issued (SC-004, FR-009)."""
    cur = _SpyCursor()

    @contextlib.contextmanager
    def fake(db_cfg):
        yield _SpyConn(cur)

    monkeypatch.setattr(db, "_session", fake)
    db_cfg = DatabaseConfig(name="d", db_name="app", user="ro", password="p")

    evil = "users; DROP TABLE users"
    with pytest.raises(InfraMcpError, match="table not found"):
        db.resolve_table_schema(db_cfg, evil)

    assert len(cur.executed) == 1
    sql, params = cur.executed[0]
    assert sql.lstrip().upper().startswith("SELECT")  # only a SELECT issued
    assert params == (evil,)  # the whole string is a bound literal, not SQL
