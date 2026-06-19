"""Synchronous SSH executor + safety utilities.

One SSHClient per call, closed immediately. Allowlist and path checks run BEFORE
any network connection. Every executed command is written to the audit log.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

import paramiko

from infra_mcp import audit
from infra_mcp.config import VMConfig
from infra_mcp.errors import CommandNotAllowedError, VMUnreachableError

CONNECT_TIMEOUT = 10
COMMAND_TIMEOUT = 30
MAX_LINES = 200
REACHABILITY_TIMEOUT = 5

# CSI SGR sequences (colors/styles): ESC [ ... m. Strip from log output to save
# tokens and noise; these escape codes are never meaningful data.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove ANSI color/style escape codes from command output."""
    return _ANSI_RE.sub("", text)


def clamp_lines(n: int, max: int = MAX_LINES) -> int:
    """Clamp a requested line count to the hard cap. Values <= max pass through."""
    if n < 1:
        return 1
    return n if n <= max else max


def check_path_allowed(path: str, dirs: list[Path]) -> str:
    """Validate a remote POSIX path against an allowlist of directories.

    Normalizes `..` traversal and rejects relative paths or anything outside the
    allowed dirs. Raises CommandNotAllowedError before any SSH connection is made.
    Returns the normalized path on success.
    """
    if not posixpath.isabs(path):
        raise CommandNotAllowedError(f"path must be absolute: {path}")
    resolved = posixpath.normpath(path)
    for d in dirs:
        allowed = posixpath.normpath(str(d).replace("\\", "/"))
        if resolved == allowed or resolved.startswith(allowed + "/"):
            return resolved
    raise CommandNotAllowedError(f"path {path} not in allowed dirs")


def check_service_allowed(vm: VMConfig, service: str) -> None:
    """Raise if the service is not in the VM's services allowlist."""
    if service not in vm.services:
        raise CommandNotAllowedError(
            f"service {service} not in allowlist for VM {vm.name}"
        )


def _connect(vm: VMConfig) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    if vm.known_hosts_file is not None:
        client.load_host_keys(str(vm.known_hosts_file.expanduser()))
    else:
        client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    kwargs: dict = dict(
        hostname=vm.host,
        username=vm.user,
        timeout=CONNECT_TIMEOUT,
        banner_timeout=CONNECT_TIMEOUT,
        auth_timeout=CONNECT_TIMEOUT,
    )
    if vm.key_path is not None:
        kwargs["key_filename"] = str(vm.key_path.expanduser())
    if vm.password is not None:
        kwargs["password"] = vm.password
        kwargs["look_for_keys"] = False
        kwargs["allow_agent"] = False
    try:
        client.connect(**kwargs)
    except Exception as e:  # paramiko + socket errors
        raise VMUnreachableError(f"VM {vm.name} unreachable: {e}") from e
    return client


def run_command(vm: VMConfig, cmd: str, audit_log_path: Path) -> tuple[str, int]:
    """Execute a command on the VM, audit it, return (stdout, exit_code).

    Caller is responsible for any allowlist/path checks BEFORE calling this.
    """
    client = _connect(vm)
    try:
        _, stdout, _ = client.exec_command(cmd, timeout=COMMAND_TIMEOUT)
        stdout.channel.settimeout(COMMAND_TIMEOUT)
        output = strip_ansi(stdout.read().decode("utf-8", errors="replace"))
        exit_code = stdout.channel.recv_exit_status()
    except VMUnreachableError:
        raise
    except Exception as e:
        client.close()
        raise VMUnreachableError(f"command failed on VM {vm.name}: {e}") from e
    finally:
        client.close()
    audit.log_command(audit_log_path, vm.name, cmd, exit_code)
    return output, exit_code


def is_reachable(vm: VMConfig) -> bool:
    """TCP connect to the SSH port (no handshake). Used by list_vms / test."""
    import socket

    try:
        with socket.create_connection((vm.host, 22), timeout=REACHABILITY_TIMEOUT):
            return True
    except OSError:
        return False
