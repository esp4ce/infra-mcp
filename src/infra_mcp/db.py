"""PostgreSQL access layer: SELECT guard, READ ONLY tx, row cap, health queries."""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import socket

import paramiko
import psycopg2
from sshtunnel import BaseSSHTunnelForwarderError, SSHTunnelForwarder

from infra_mcp import audit
from infra_mcp.config import DatabaseConfig
from infra_mcp.errors import (
    InfraMcpError,
    QueryError,
    ReadOnlyViolationError,
    VMUnreachableError,
)

DEFAULT_ROWS = 50
MAX_ROWS = 100
CATALOG_CAP = 200  # server-side hard cap for catalog reads; caller cannot raise it
CONNECT_TIMEOUT = 10
STATEMENT_TIMEOUT_MS = 30000  # cap query runtime; connect_timeout covers connect only
SSH_PORT = 22

_SELECT_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)


def guard_select(sql: str) -> None:
    """Allow only a single SELECT statement. Raises ReadOnlyViolationError otherwise.

    Called BEFORE any connection is opened.
    """
    if not _SELECT_RE.match(sql):
        raise ReadOnlyViolationError(f"Only SELECT allowed; got: {sql[:80]}")
    # Reject multi-statement: any non-whitespace content after a trailing-stripped ';'
    if re.search(r";.*\S", sql.rstrip().rstrip(";"), re.DOTALL):
        raise ReadOnlyViolationError("Multi-statement SQL not allowed")


def clamp_rows(n: int, max_rows: int = MAX_ROWS) -> int:
    if n < 1:
        return 1
    return n if n <= max_rows else max_rows


def _inject_limit(sql: str, limit: int) -> str:
    """Append LIMIT if the query has no LIMIT clause already."""
    stripped = sql.rstrip().rstrip(";")
    if _LIMIT_RE.search(stripped):
        return stripped
    return f"{stripped} LIMIT {limit}"


def _verify_host_key(db: DatabaseConfig) -> None:
    """Reject unknown/mismatched SSH host keys before opening the tunnel.

    Mirrors ssh.py's RejectPolicy: load known_hosts (the DB's configured file, else
    ~/.ssh/known_hosts), fetch the remote server key over a keyless transport, and
    raise VMUnreachableError if it is absent or does not match.
    """
    hostkeys = paramiko.HostKeys()
    if db.ssh_known_hosts_file is not None:
        hostkeys.load(str(db.ssh_known_hosts_file.expanduser()))
    else:
        sys_kh = Path("~/.ssh/known_hosts").expanduser()
        if sys_kh.exists():
            hostkeys.load(str(sys_kh))
    try:
        sock = socket.create_connection((db.ssh_host, SSH_PORT), timeout=CONNECT_TIMEOUT)
    except OSError as e:
        raise VMUnreachableError(f"database {db.name} unreachable: {e}") from e
    transport = paramiko.Transport(sock)
    try:
        transport.start_client(timeout=CONNECT_TIMEOUT)
        remote_key = transport.get_remote_server_key()
    except paramiko.SSHException as e:
        raise VMUnreachableError(
            f"database {db.name} host key check failed: {e}"
        ) from e
    finally:
        transport.close()
    if not hostkeys.check(db.ssh_host, remote_key):
        raise VMUnreachableError(
            f"database {db.name}: unknown or mismatched SSH host key for {db.ssh_host}"
        )


@contextmanager
def _session(db: DatabaseConfig):
    """Yield a READ ONLY psycopg2 connection over an ephemeral SSH tunnel.

    Opens a fresh SSHTunnelForwarder to the DB's parent VM (random local port
    forwarded to the remote PG bind), connects psycopg2 through it, and on exit
    closes the connection first, then stops the tunnel — guaranteed even on error.
    No persistent tunnel or connection is kept.
    """
    _verify_host_key(db)
    kwargs: dict = dict(
        ssh_username=db.ssh_user,
        remote_bind_address=(db.host, db.port),
    )
    if db.ssh_key_path is not None:
        kwargs["ssh_pkey"] = str(db.ssh_key_path.expanduser())
    if db.ssh_password is not None:
        kwargs["ssh_password"] = db.ssh_password
    try:
        tunnel = SSHTunnelForwarder((db.ssh_host, SSH_PORT), **kwargs)
        tunnel.start()
    except (BaseSSHTunnelForwarderError, OSError) as e:
        raise VMUnreachableError(f"database {db.name} tunnel failed: {e}") from e
    conn = None
    try:
        conn = psycopg2.connect(
            dbname=db.db_name,
            user=db.user,
            password=db.password,
            host="127.0.0.1",
            port=tunnel.local_bind_port,
            connect_timeout=CONNECT_TIMEOUT,
            # Cap runtime at the libpq level so a slow/runaway SELECT (pg_sleep,
            # heavy join) can't hang the agent or hold the tunnel. READ ONLY blocks
            # writes, not cost. Set here = no extra round-trip, applies session-wide.
            options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
        )
        conn.set_session(readonly=True, autocommit=False)
    except psycopg2.Error as e:
        if conn is not None:
            conn.close()
        tunnel.stop()
        raise VMUnreachableError(f"database {db.name} unreachable: {e}") from e
    try:
        yield conn
    finally:
        conn.close()
        tunnel.stop()


def run_select(
    db: DatabaseConfig, sql: str, limit: int, audit_log_path: Path
) -> tuple[list[str], list[tuple]]:
    """Guard, connect READ ONLY, inject LIMIT, run, audit. Returns (columns, rows)."""
    guard_select(sql)
    final_sql = _inject_limit(sql, clamp_rows(limit))
    with _session(db) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(final_sql)
                columns = [c.name for c in cur.description] if cur.description else []
                rows = cur.fetchall()
            conn.rollback()
        except psycopg2.Error as e:
            raise QueryError(str(e).strip()) from e
    audit.log_command(audit_log_path, db.name, final_sql, 0)
    return columns, rows


def get_health(db: DatabaseConfig, audit_log_path: Path) -> dict[str, int]:
    """Return connection counts, waiting locks, and long-running query count."""
    queries = {
        "total_connections": "SELECT count(*) FROM pg_stat_activity",
        "idle_connections": "SELECT count(*) FROM pg_stat_activity WHERE state = 'idle'",
        "waiting_locks": "SELECT count(*) FROM pg_locks WHERE NOT granted",
        "long_running_queries": (
            "SELECT count(*) FROM pg_stat_activity "
            f"WHERE query_start < now() - interval '{db.slow_query_ms} milliseconds' "
            "AND state = 'active'"
        ),
    }
    result: dict[str, int] = {}
    with _session(db) as conn:
        try:
            with conn.cursor() as cur:
                for metric, q in queries.items():
                    cur.execute(q)
                    result[metric] = cur.fetchone()[0]
            conn.rollback()
        except psycopg2.Error as e:
            raise QueryError(str(e).strip()) from e
    audit.log_command(audit_log_path, db.name, "get_db_status (health queries)", 0)
    return result


# --- Catalog (schema-aware) reads -------------------------------------------


@dataclass
class TableRef:
    schema: str
    name: str


@dataclass
class ColumnRef:
    name: str
    type: str


@dataclass
class ForeignKeyRef:
    column: str
    references: str  # referenced schema.table
    ref_column: str


@dataclass
class TableListResult:
    tables: list[TableRef]
    total: int
    truncated: bool


@dataclass
class TableDescription:
    columns: list[ColumnRef]
    primary_key: list[str]
    foreign_keys: list[ForeignKeyRef]
    total_columns: int
    truncated: bool


def truncation_marker(cap: int, total: int, unit: str) -> str:
    """The single trailing marker line appended when a catalog read is capped."""
    return f"-- TRUNCATED: showing {cap} of {total} {unit}"


def bound_catalog(rows: list, total: int, unit: str, cap: int = CATALOG_CAP):
    """Cap `rows` at `cap`; return (capped_rows, marker_or_None) per FR-005/FR-013."""
    if total > cap:
        return rows[:cap], truncation_marker(cap, total, unit)
    return rows, None


def _list_tables_sql(limit: int) -> str:
    return (
        "SELECT table_schema, table_name "
        "FROM information_schema.tables "
        "WHERE table_type = 'BASE TABLE' "
        "AND table_schema NOT IN ('pg_catalog', 'information_schema') "
        "ORDER BY table_schema, table_name "
        f"LIMIT {limit}"
    )


def _count_tables_sql() -> str:
    return (
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_type = 'BASE TABLE' "
        "AND table_schema NOT IN ('pg_catalog', 'information_schema')"
    )


def _resolve_schema_sql() -> str:
    return (
        "SELECT table_schema FROM information_schema.tables "
        "WHERE table_name = %s "
        "AND table_schema NOT IN ('pg_catalog', 'information_schema') "
        "ORDER BY table_schema"
    )


def _columns_sql(limit: int) -> str:
    return (
        "SELECT column_name, data_type "
        "FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s "
        "ORDER BY ordinal_position "
        f"LIMIT {limit}"
    )


def _count_columns_sql() -> str:
    return (
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s"
    )


def _primary_key_sql() -> str:
    return (
        "SELECT kcu.column_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name = kcu.constraint_name "
        "  AND tc.table_schema = kcu.table_schema "
        "WHERE tc.constraint_type = 'PRIMARY KEY' "
        "AND tc.table_schema = %s AND tc.table_name = %s "
        "ORDER BY kcu.ordinal_position"
    )


def _foreign_keys_sql() -> str:
    return (
        "SELECT kcu.column_name, ccu.table_schema, ccu.table_name, ccu.column_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name = kcu.constraint_name "
        "  AND tc.table_schema = kcu.table_schema "
        "JOIN information_schema.constraint_column_usage ccu "
        "  ON tc.constraint_name = ccu.constraint_name "
        "  AND tc.table_schema = ccu.table_schema "
        "WHERE tc.constraint_type = 'FOREIGN KEY' "
        "AND tc.table_schema = %s AND tc.table_name = %s "
        "ORDER BY kcu.ordinal_position"
    )


def list_tables(
    db: DatabaseConfig, audit_log_path: Path, cap: int = CATALOG_CAP
) -> TableListResult:
    """Read user tables from information_schema (READ ONLY). Bounded at `cap`.

    `LIMIT cap+1` detects overflow; an exact `total` is fetched with a follow-up
    count(*) ONLY on overflow (non-truncated path: total = rows returned).
    """
    list_sql = _list_tables_sql(cap + 1)
    guard_select(list_sql)
    with _session(db) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(list_sql)
                fetched = cur.fetchall()
                truncated = len(fetched) > cap
                if truncated:
                    cur.execute(_count_tables_sql())
                    total = cur.fetchone()[0]
                else:
                    total = len(fetched)
            conn.rollback()
        except psycopg2.Error as e:
            raise QueryError(str(e).strip()) from e
    tables = [TableRef(schema=r[0], name=r[1]) for r in fetched[:cap]]
    audit.log_command(audit_log_path, db.name, "list_tables (information_schema)", 0)
    return TableListResult(tables=tables, total=total, truncated=truncated)


def resolve_table_schema(db: DatabaseConfig, table: str) -> str:
    """Resolve a bare table name to its schema via information_schema (READ ONLY).

    The name is bound as a `%s` parameter (never interpolated), so a
    mutation-shaped value simply finds no rows → not found (SC-004). Raises
    InfraMcpError for not-found or ambiguous results.
    """
    sql = _resolve_schema_sql()
    guard_select(sql)
    with _session(db) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (table,))
                schemas = [r[0] for r in cur.fetchall()]
            conn.rollback()
        except psycopg2.Error as e:
            raise QueryError(str(e).strip()) from e
    if not schemas:
        raise InfraMcpError(f"table not found: {table}")
    if len(schemas) == 1:
        return schemas[0]
    if "public" in schemas:
        return "public"
    raise InfraMcpError(
        f"ambiguous table: {table} (in schemas: {', '.join(schemas)})"
    )


def describe_table(
    db: DatabaseConfig,
    schema: str,
    table: str,
    audit_log_path: Path,
    cap: int = CATALOG_CAP,
) -> TableDescription | None:
    """Read columns + PK + FK for one table (READ ONLY). None if the table has no
    columns (i.e. does not exist). Identifiers are always bound parameters."""
    cols_sql = _columns_sql(cap + 1)
    guard_select(cols_sql)
    params = (schema, table)
    with _session(db) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(cols_sql, params)
                fetched_cols = cur.fetchall()
                truncated = len(fetched_cols) > cap
                if truncated:
                    cur.execute(_count_columns_sql(), params)
                    total_columns = cur.fetchone()[0]
                else:
                    total_columns = len(fetched_cols)
                if not fetched_cols:
                    conn.rollback()
                    return None
                cur.execute(_primary_key_sql(), params)
                pk = [r[0] for r in cur.fetchall()]
                cur.execute(_foreign_keys_sql(), params)
                fks = [
                    ForeignKeyRef(
                        column=r[0], references=f"{r[1]}.{r[2]}", ref_column=r[3]
                    )
                    for r in cur.fetchall()
                ]
            conn.rollback()
        except psycopg2.Error as e:
            raise QueryError(str(e).strip()) from e
    columns = [ColumnRef(name=r[0], type=r[1]) for r in fetched_cols[:cap]]
    audit.log_command(
        audit_log_path, db.name, f"describe_table {schema}.{table} (information_schema)", 0
    )
    return TableDescription(
        columns=columns,
        primary_key=pk,
        foreign_keys=fks,
        total_columns=total_columns,
        truncated=truncated,
    )
