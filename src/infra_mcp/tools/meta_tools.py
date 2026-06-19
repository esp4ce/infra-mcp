"""Aggregate / local MCP tools: get_infra_overview, get_audit_log."""

from __future__ import annotations

from infra_mcp import audit, db, runtime
from infra_mcp.errors import InfraMcpError, VMUnreachableError
from infra_mcp.ssh import clamp_lines
from infra_mcp.tools import ssh_tools

_OVERVIEW_DESC = (
    "Returns service statuses and database health for a named VM in a single call. "
    "Call this first when diagnosing any VM-level issue. Unreachable services or "
    "databases are listed with an error note rather than failing the whole call."
)
_AUDIT_DESC = "Returns recent entries from the local audit log (oldest first)."


def get_infra_overview(vm: str) -> str:
    """Aggregate service statuses + DB health for one VM into a single response."""
    try:
        vm_cfg = ssh_tools._require_vm(vm)
    except InfraMcpError as e:
        return f"ERROR: {e}"
    audit_path = runtime.get_audit_path()

    svc_lines = ["=== Services ===", "service\tstate\tuptime"]
    for service in vm_cfg.services:
        try:
            state, uptime = ssh_tools._service_state(vm_cfg, service, audit_path)
            svc_lines.append(f"{service}\t{state}\t{uptime}")
        except VMUnreachableError as e:
            svc_lines.append(f"{service}\tERROR\t{e}")

    db_lines = [
        "",
        "=== Databases ===",
        "db\ttotal_conn\tidle\twaiting_locks\tlong_running",
    ]
    for db_cfg in vm_cfg.databases:
        try:
            health = db.get_health(db_cfg, audit_path)
            db_lines.append(
                f"{db_cfg.name}\t{health['total_connections']}\t"
                f"{health['idle_connections']}\t{health['waiting_locks']}\t"
                f"{health['long_running_queries']}"
            )
        except VMUnreachableError as e:
            db_lines.append(f"{db_cfg.name}\tERROR: {e}")

    return "\n".join(svc_lines + db_lines)


def get_audit_log(lines: int = 50) -> str:
    """Return the last N entries from the local audit log. Reads local file only."""
    n = clamp_lines(lines)
    entries = audit.read_tail(runtime.get_audit_path(), n)
    return "\n".join(entries) if entries else "(audit log empty)"


def register(mcp) -> None:
    mcp.tool(description=_OVERVIEW_DESC)(get_infra_overview)
    mcp.tool(description=_AUDIT_DESC)(get_audit_log)
