<div align="center">

# infra-mcp

[![PyPI](https://img.shields.io/pypi/v/infra-mcp)](https://pypi.org/project/infra-mcp/)
[![Glama](https://img.shields.io/badge/Glama-grey?logo=data:image/svg+xml;base64,PHN2ZyBmaWxsPSJub25lIiB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyMjAgMzAwIj48cGF0aCBkPSJNNDAgMEgyMFYxNDBINDBWMFpNMjAwIDBIMTgwVjE0MEgyMDBWMFpNMjAgMTBIMTBWMTEwSDIwVjEwWk0yMTAgMTBIMjAwVjExMEgyMTBWMTBaTTEwIDIwSDBWODBIMTBWMjBaTTIyMCAyMEgyMTBWODBIMjIwVjIwWk01MCA2MEg0MFYxNDBINTBWNjBaTTE4MCA2MEgxNzBWMTQwSDE4MFY2MFpNMTUwIDcwSDcwVjE3MEgxNTBWNzBaTTcwIDgwSDUwVjE0MEg3MFY4MFpNMTcwIDgwSDE1MFYxNDBIMTcwVjgwWk0yMCAxMzBIMTBWMTQwSDIwVjEzMFpNMjEwIDEzMEgyMDBWMTQwSDIxMFYxMzBaTTcwIDE0MEg2MFYyMTBINzBWMTQwWk0xNjAgMTQwSDE1MFYyMTBIMTYwVjE0MFpNMzAgMTUwSDIwVjMwMEgzMFYxNTBaTTYwIDE1MEg1MFYzMDBINjBWMTUwWk0xNzAgMTUwSDE2MFYzMDBIMTcwVjE1MFpNMjAwIDE1MEgxOTBWMzAwSDIwMFYxNTBaTTUwIDE2MEgzMFYzMDBINTBWMTYwWk0xOTAgMTYwSDE3MFYzMDBIMTkwVjE2MFpNODAgMTcwSDcwVjIyMEg4MFYxNzBaTTEzMCAxNzBIOTBWMTgwSDEzMFYxNzBaTTE1MCAxNzBIMTQwVjIyMEgxNTBWMTcwWk0yMCAxODBIMTBWMjYwSDIwVjE4MFpNOTAgMTgwSDgwVjIyMEg5MFYxODBaTTE0MCAxODBIMTMwVjIyMEgxNDBWMTgwWk0yMTAgMTgwSDIwMFYyNjBIMjEwVjE4MFpNMTAwIDE5MEg5MFYyMjBIMTAwVjE5MFpNMTMwIDE5MEgxMjBWMjIwSDEzMFYxOTBaTTcwIDIyMEg2MFYzMDBINzBWMjIwWk0xNjAgMjIwSDE1MFYzMDBIMTYwVjIyMFpNOTAgMjMwSDcwVjMwMEg5MFYyMzBaTTE1MCAyMzBIMTMwVjMwMEgxNTBWMjMwWk0xMzAgMjUwSDkwVjMwMEgxMzBWMjUwWiIgZmlsbD0id2hpdGUiLz48L3N2Zz4=&logoColor=white)](https://glama.ai/mcp/servers/esp4ce/infra-mcp)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

*Read-only MCP access to on-prem Linux VMs and PostgreSQL databases over SSH.*

```
 agent ──stdio──▶ infra-mcp ──SSH──▶  VMs  (journald · log files)
                                  └──▶  DBs  (read-only PostgreSQL)
```

</div>

---

An agent can check service health, retrieve bounded logs, inspect DB state, and explore table schemas — without terminal access. Every remote operation is allowlist-gated and written to an append-only audit log.

## Install

```bash
uv tool install infra-mcp
# or
pip install infra-mcp
```

## Configure

Copy `infra-mcp.yaml.example` to `~/.infra-mcp/infra-mcp.yaml` and edit it.

```bash
# Generate a starter config from ~/.ssh/config
infra-mcp generate-config -o ~/.infra-mcp/infra-mcp.yaml

# Create the read-only PostgreSQL role(s)
infra-mcp setup

# Check VM reachability
infra-mcp test

# Refresh discovered services, log dirs, and databases (updates config in place)
infra-mcp discover --in-place
```

Override the config path with `--config` or `INFRA_MCP_CONFIG`.

## Run

```bash
infra-mcp run
```

Register as a **stdio** MCP server in your client (Claude Code, Cursor, …) with command `infra-mcp run`.

## Updates

`infra-mcp` checks PyPI for a newer release once a day and, when one exists, prints a
one-line hint to stderr telling you how to upgrade:

```bash
uv tool upgrade infra-mcp   # or: pip install --upgrade infra-mcp
```

The check runs in the background, never blocks startup, and never touches stdout. Print
the installed version with `infra-mcp --version`. Disable the check entirely by setting
`INFRA_MCP_NO_UPDATE_CHECK=1`.

## Tools

### VM & services

| Tool | Purpose |
|------|---------|
| `list_vms` | All VMs with reachability and watched services |
| `get_infra_overview` | Service states + DB health for one VM in a single call |
| `get_service_status` | systemd state, uptime, last 5 log lines |
| `get_service_logs` | Bounded journald logs, filtered by severity |
| `get_log_file` | Last N lines of an allowed log file, optional grep |

### Databases

| Tool | Purpose |
|------|---------|
| `get_db_status` | Connection counts, waiting locks, long-running query count |
| `query_db` | Bounded caller-supplied `SELECT` |
| `list_tables` | Tables in a database (schema + name), capped at 200 |
| `describe_table` | Columns, types, primary key, foreign keys for one table |

### Meta

| Tool | Purpose |
|------|---------|
| `get_audit_log` | Recent entries from the local audit log |

All output is bounded server-side (200 log lines, 100 DB rows, 200 tables/columns max). Truncation is always flagged with a `-- TRUNCATED:` marker. `list_tables` and `describe_table` cache results in memory (TTL: `schema_cache_ttl_hours`, default 24 h); pass `refresh: true` to force a live re-read.

## Security model

- SSH commands and systemd services are checked against a per-VM allowlist before any network call.
- All DB queries run as a read-only role inside a `READ ONLY` transaction.
- Log file paths are resolved against a per-VM directory allowlist (`..` traversal blocked).
- Every remote operation is appended to a local JSONL audit log.
