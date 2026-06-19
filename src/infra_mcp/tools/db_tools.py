"""PostgreSQL-backed MCP tools: get_db_status, query_db, list_tables, describe_table."""

from __future__ import annotations

from datetime import datetime, timezone

from infra_mcp import runtime, schema_cache
from infra_mcp.errors import InfraMcpError

_DB_STATUS_DESC = (
    "Returns health metrics for a configured database: connection counts, "
    "waiting locks, and long-running query count."
)
_QUERY_DB_DESC = (
    "Executes a caller-supplied SELECT statement against a configured database, "
    "bounded by row limit. Only SELECT is permitted."
)
_LIST_TABLES_DESC = (
    "Lists the tables of a configured database with their schema/namespace, in one "
    "bounded call — no column detail. Call this first to decide which tables are "
    "relevant before describing them. Set refresh=true to force a live re-read."
)
_DESCRIBE_TABLE_DESC = (
    "Describes one named table — its columns and types, primary key, and "
    "foreign-key relationships — scoped to just that table, so you can author a "
    "correct (possibly multi-table) query directly. Indexes and comments are not "
    "included. Set refresh=true to force a live re-read."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_db(name: str):
    found = runtime.get_config().find_db(name)
    if found is None:
        raise InfraMcpError(f"unknown database: {name}")
    return found[1]


def get_db_status(db: str) -> str:  # noqa: A002 - matches contract param name
    """Return connection counts, waiting locks, and long-running query count (TSV)."""
    from infra_mcp import db as dbmod

    try:
        db_cfg = _require_db(db)
        health = dbmod.get_health(db_cfg, runtime.get_audit_path())
        return "\n".join(f"{metric}\t{value}" for metric, value in health.items())
    except InfraMcpError as e:
        return f"ERROR: {e}"


def query_db(db: str, sql: str, limit: int = 50) -> str:  # noqa: A002
    """Run a bounded SELECT against a configured database. Returns TSV with header."""
    from infra_mcp import db as dbmod

    try:
        db_cfg = _require_db(db)
        columns, rows = dbmod.run_select(db_cfg, sql, limit, runtime.get_audit_path())
        out = ["\t".join(columns)]
        for row in rows:
            out.append("\t".join("" if v is None else str(v) for v in row))
        return "\n".join(out)
    except InfraMcpError as e:
        return f"ERROR: {e}"


def _render_table_list(entry: dict) -> str:
    lines = ["schema\ttable"]
    tables = entry["tables"]
    if not tables:
        lines.append("-- no user tables")
        return "\n".join(lines)
    for t in tables:
        lines.append(f"{t['schema']}\t{t['name']}")
    if entry["truncated"]:
        lines.append(
            f"-- TRUNCATED: showing {len(tables)} of {entry['total']} tables"
        )
    return "\n".join(lines)


def _render_description(entry: dict) -> str:
    cols = entry["columns"]
    lines = [f"table: {entry['qualified']}", "=== columns ===", "name\ttype"]
    for c in cols:
        lines.append(f"{c['name']}\t{c['type']}")
    if entry["truncated"]:
        lines.append(
            f"-- TRUNCATED: showing {len(cols)} of {entry['total_columns']} columns"
        )
    lines.append("=== primary key ===")
    lines.extend(entry["primary_key"])
    lines.append("=== foreign keys ===")
    if entry["foreign_keys"]:
        lines.append("column\treferences\tref_column")
        for f in entry["foreign_keys"]:
            lines.append(f"{f['column']}\t{f['references']}\t{f['ref_column']}")
    return "\n".join(lines)


def list_tables(db: str, refresh: bool = False) -> str:  # noqa: A002
    """List a database's tables (schema<TAB>table TSV). Cache-first, bounded at 200."""
    from infra_mcp import db as dbmod

    try:
        db_cfg = _require_db(db)
        ttl = runtime.get_schema_cache_ttl()
        entry = None if refresh else schema_cache.get_table_list(db, ttl)
        if entry is None:
            result = dbmod.list_tables(db_cfg, runtime.get_audit_path())
            entry = {
                "fetched_at": _now_iso(),
                "tables": [{"schema": t.schema, "name": t.name} for t in result.tables],
                "truncated": result.truncated,
                "total": result.total,
            }
            schema_cache.put_table_list(db, entry)
        return _render_table_list(entry)
    except InfraMcpError as e:
        return f"ERROR: {e}"


def describe_table(db: str, table: str, refresh: bool = False) -> str:  # noqa: A002
    """Describe one table's columns, primary key, and foreign keys. Cache-first."""
    from infra_mcp import db as dbmod

    try:
        db_cfg = _require_db(db)
        ttl = runtime.get_schema_cache_ttl()
        if "." in table:
            schema, name = table.split(".", 1)
        else:
            schema = dbmod.resolve_table_schema(db_cfg, table)
            name = table
        key = f"{schema}.{name}"
        entry = None if refresh else schema_cache.get_description(db, key, ttl)
        if entry is None:
            desc = dbmod.describe_table(db_cfg, schema, name, runtime.get_audit_path())
            if desc is None:
                raise InfraMcpError(f"table not found: {key}")
            entry = {
                "fetched_at": _now_iso(),
                "qualified": key,
                "columns": [{"name": c.name, "type": c.type} for c in desc.columns],
                "primary_key": desc.primary_key,
                "foreign_keys": [
                    {
                        "column": f.column,
                        "references": f.references,
                        "ref_column": f.ref_column,
                    }
                    for f in desc.foreign_keys
                ],
                "truncated": desc.truncated,
                "total_columns": desc.total_columns,
            }
            schema_cache.put_description(db, key, entry)
        return _render_description(entry)
    except InfraMcpError as e:
        return f"ERROR: {e}"


def register(mcp) -> None:
    mcp.tool(description=_DB_STATUS_DESC)(get_db_status)
    mcp.tool(description=_QUERY_DB_DESC)(query_db)
    mcp.tool(description=_LIST_TABLES_DESC)(list_tables)
    mcp.tool(description=_DESCRIBE_TABLE_DESC)(describe_table)
