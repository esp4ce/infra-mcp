<div align="center">

# infra-mcp

[![PyPI](https://img.shields.io/pypi/v/infra-mcp)](https://pypi.org/project/infra-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/infra-mcp)](https://pypi.org/project/infra-mcp/)
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
