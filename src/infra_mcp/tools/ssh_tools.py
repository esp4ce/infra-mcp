"""SSH-backed MCP tools: list_vms, get_service_status, get_service_logs, get_log_file."""

from __future__ import annotations

import shlex

from infra_mcp import runtime, ssh
from infra_mcp.errors import InfraMcpError, VMUnreachableError

_LEVELS = {"error", "warning", "info", "debug"}

_LIST_VMS_DESC = (
    "Returns all configured VMs with reachability status and watched service names. "
    "Call this first in any session to discover available infrastructure."
)
_SERVICE_STATUS_DESC = (
    "Returns systemd state, uptime, and last 5 log lines for a service on a VM. "
    "Use for a quick health check before requesting full logs."
)
_SERVICE_LOGS_DESC = (
    "Returns bounded journald logs for a service, filtered by severity level. "
    "Filtering and line-capping execute on the VM before transmission."
)
_LOG_FILE_DESC = (
    "Returns the last N lines of a log file on a VM, with optional grep pattern. "
    "Both grep and line-capping execute on the VM before any data is transmitted."
)

_STATE_BY_CODE = {0: "active", 3: "inactive", 1: "failed"}


def _require_vm(name: str):
    vm = runtime.get_config().find_vm(name)
    if vm is None:
        raise InfraMcpError(f"unknown VM: {name}")
    return vm


def list_vms() -> str:
    """Return all configured VMs with reachability and watched services (no IPs)."""
    lines = ["name\tstatus\tservices"]
    for vm in runtime.get_config().vms:
        status = "reachable" if ssh.is_reachable(vm) else "unreachable"
        services = ",".join(vm.services) if vm.services else "-"
        lines.append(f"{vm.name}\t{status}\t{services}")
    return "\n".join(lines)


def _service_state(vm, service: str, audit_path) -> tuple[str, str]:
    out, code = ssh.run_command(vm, f"systemctl is-active {service}", audit_path)
    state = _STATE_BY_CODE.get(code, "unknown")
    uptime_cmd = (
        f"ps -o etime= -p $(systemctl show -p MainPID --value {service}) 2>/dev/null"
    )
    up_out, _ = ssh.run_command(vm, uptime_cmd, audit_path)
    uptime = up_out.strip() or "unknown"
    return state, uptime


def get_service_status(vm: str, service: str) -> str:
    """Return systemd state, uptime, and last 5 log lines for a service."""
    try:
        vm_cfg = _require_vm(vm)
        ssh.check_service_allowed(vm_cfg, service)
        audit_path = runtime.get_audit_path()
        state, uptime = _service_state(vm_cfg, service, audit_path)
        logs, _ = ssh.run_command(
            vm_cfg, f"journalctl -u {service} -n 5 --no-pager", audit_path
        )
        return f"state: {state}\nuptime: {uptime}\n---\n{logs.rstrip()}"
    except InfraMcpError as e:
        return f"ERROR: {e}"


def get_service_logs(
    vm: str, service: str, level: str = "error", lines: int = 50
) -> str:
    """Return bounded journald logs for a service, filtered by severity level."""
    try:
        vm_cfg = _require_vm(vm)
        ssh.check_service_allowed(vm_cfg, service)
        if level not in _LEVELS:
            raise InfraMcpError(f"invalid level {level}; one of {sorted(_LEVELS)}")
        n = ssh.clamp_lines(lines)
        cmd = f"journalctl -u {service} -n {n} -p {level} --no-pager"
        out, _ = ssh.run_command(vm_cfg, cmd, runtime.get_audit_path())
        return out.rstrip() or "(no matching log lines)"
    except InfraMcpError as e:
        return f"ERROR: {e}"


def get_log_file(vm: str, path: str, lines: int = 50, pattern: str | None = None) -> str:
    """Return last N lines of a log file on a VM, with optional grep pattern."""
    try:
        vm_cfg = _require_vm(vm)
        # Path allowlist check BEFORE any SSH connection.
        safe_path = ssh.check_path_allowed(path, vm_cfg.log_dirs)
        # Shell-quote the path: allowlist normpath does NOT strip shell metachars
        # (;, $(), backticks, spaces), so an unquoted path is a command-injection hole.
        quoted_path = shlex.quote(safe_path)
        n = ssh.clamp_lines(lines)
        if pattern:
            escaped = pattern.replace("'", "'\\''")
            cmd = f"tail -n {n} {quoted_path} | grep -E '{escaped}'"
        else:
            cmd = f"tail -n {n} {quoted_path}"
        out, _ = ssh.run_command(vm_cfg, cmd, runtime.get_audit_path())
        return out.rstrip() or "(no matching lines)"
    except VMUnreachableError as e:
        return f"ERROR: {e}"
    except InfraMcpError as e:
        return f"ERROR: {e}"


def register(mcp) -> None:
    mcp.tool(description=_LIST_VMS_DESC)(list_vms)
    mcp.tool(description=_SERVICE_STATUS_DESC)(get_service_status)
    mcp.tool(description=_SERVICE_LOGS_DESC)(get_service_logs)
    mcp.tool(description=_LOG_FILE_DESC)(get_log_file)
