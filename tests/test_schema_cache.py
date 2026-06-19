"""In-memory schema cache: TTL freshness, mixed-granularity get/put, refresh, invalidate."""

from datetime import datetime, timedelta, timezone

import pytest

from infra_mcp import db, runtime, schema_cache
from infra_mcp.config import DatabaseConfig
from infra_mcp.tools import db_tools


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fresh_ts() -> str:
    return _iso(datetime.now(timezone.utc))


def _stale_ts(hours: int) -> str:
    return _iso(datetime.now(timezone.utc) - timedelta(hours=hours))


@pytest.fixture(autouse=True)
def _clean_cache():
    schema_cache.clear()
    yield
    schema_cache.clear()


# --- T007: table_list get/put + cache-hit reuse -----------------------------


def test_table_list_put_then_get_fresh():
    entry = {"fetched_at": _fresh_ts(), "tables": [], "truncated": False, "total": 0}
    schema_cache.put_table_list("prod-db", entry)
    assert schema_cache.get_table_list("prod-db", ttl_hours=24) is entry


def test_table_list_miss_returns_none():
    assert schema_cache.get_table_list("never", ttl_hours=24) is None


# --- T013: description cache keyed by schema.table, independent fetched_at ---


def test_description_put_get_keyed():
    e1 = {"fetched_at": _fresh_ts(), "columns": []}
    e2 = {"fetched_at": _fresh_ts(), "columns": []}
    schema_cache.put_description("prod-db", "public.orders", e1)
    schema_cache.put_description("prod-db", "public.customers", e2)
    assert schema_cache.get_description("prod-db", "public.orders", 24) is e1
    assert schema_cache.get_description("prod-db", "public.customers", 24) is e2
    assert schema_cache.get_description("prod-db", "public.nope", 24) is None


def test_description_independent_of_table_list():
    schema_cache.put_table_list(
        "prod-db", {"fetched_at": _stale_ts(48), "tables": [], "truncated": False, "total": 0}
    )
    schema_cache.put_description(
        "prod-db", "public.orders", {"fetched_at": _fresh_ts(), "columns": []}
    )
    # table_list stale → miss, but the description is independently fresh → hit
    assert schema_cache.get_table_list("prod-db", 24) is None
    assert schema_cache.get_description("prod-db", "public.orders", 24) is not None


# --- T016: freshness, refresh bypass, invalidate ----------------------------


def test_is_fresh_boundary():
    assert schema_cache.is_fresh(_stale_ts(1), ttl_hours=24) is True
    assert schema_cache.is_fresh(_stale_ts(25), ttl_hours=24) is False


def test_stale_table_list_treated_as_miss():
    schema_cache.put_table_list(
        "prod-db", {"fetched_at": _stale_ts(25), "tables": [], "truncated": False, "total": 0}
    )
    assert schema_cache.get_table_list("prod-db", ttl_hours=24) is None


def test_invalidate_removes_all_db_entries():
    schema_cache.put_table_list(
        "prod-db", {"fetched_at": _fresh_ts(), "tables": [], "truncated": False, "total": 0}
    )
    schema_cache.put_description(
        "prod-db", "public.orders", {"fetched_at": _fresh_ts(), "columns": []}
    )
    schema_cache.invalidate("prod-db")
    assert schema_cache.get_table_list("prod-db", 24) is None
    assert schema_cache.get_description("prod-db", "public.orders", 24) is None


# --- Tool-level cache-hit reuse and refresh bypass --------------------------


def _wire_tool(monkeypatch, calls):
    db_cfg = DatabaseConfig(name="prod-db", db_name="app", user="ro", password="p")
    monkeypatch.setattr(db_tools, "_require_db", lambda name: db_cfg)
    monkeypatch.setattr(runtime, "get_schema_cache_ttl", lambda: 24)
    monkeypatch.setattr(runtime, "get_audit_path", lambda: None)

    def fake_list(db_cfg_, audit_log_path, cap=200):
        calls.append("live")
        return db.TableListResult(
            tables=[db.TableRef("public", "orders")], total=1, truncated=False
        )

    monkeypatch.setattr(db, "list_tables", fake_list)


def test_fresh_cache_hit_makes_no_live_read(monkeypatch):
    calls = []
    _wire_tool(monkeypatch, calls)
    schema_cache.put_table_list(
        "prod-db",
        {
            "fetched_at": _fresh_ts(),
            "tables": [{"schema": "public", "name": "cached"}],
            "truncated": False,
            "total": 1,
        },
    )
    out = db_tools.list_tables("prod-db")
    assert calls == []  # served from cache, no live read
    assert "public\tcached" in out


def test_refresh_true_bypasses_fresh_entry(monkeypatch):
    calls = []
    _wire_tool(monkeypatch, calls)
    schema_cache.put_table_list(
        "prod-db",
        {
            "fetched_at": _fresh_ts(),
            "tables": [{"schema": "public", "name": "cached"}],
            "truncated": False,
            "total": 1,
        },
    )
    out = db_tools.list_tables("prod-db", refresh=True)
    assert calls == ["live"]  # refresh forced a live read despite a fresh entry
    assert "public\torders" in out
