"""Unit tests for the log-file path allowlist."""

from pathlib import Path
from unittest import mock

import pytest

from infra_mcp.errors import CommandNotAllowedError
from infra_mcp.ssh import check_path_allowed

ALLOWED = [Path("/var/log/nginx"), Path("/var/log/haproxy")]


def test_exact_allowed_dir_passes():
    assert check_path_allowed("/var/log/nginx", ALLOWED) == "/var/log/nginx"


def test_subpath_passes():
    assert check_path_allowed("/var/log/nginx/access.log", ALLOWED) == (
        "/var/log/nginx/access.log"
    )


def test_traversal_rejected():
    with pytest.raises(CommandNotAllowedError):
        check_path_allowed("/var/log/nginx/../../etc/passwd", ALLOWED)


def test_unrelated_dir_rejected():
    with pytest.raises(CommandNotAllowedError):
        check_path_allowed("/etc/passwd", ALLOWED)


def test_relative_path_rejected():
    with pytest.raises(CommandNotAllowedError):
        check_path_allowed("var/log/nginx/access.log", ALLOWED)


def test_prefix_lookalike_rejected():
    # /var/log/nginx-evil must not match /var/log/nginx
    with pytest.raises(CommandNotAllowedError):
        check_path_allowed("/var/log/nginx-evil/x.log", ALLOWED)


def test_no_ssh_connection_when_path_rejected():
    with mock.patch("paramiko.SSHClient.connect") as connect:
        with pytest.raises(CommandNotAllowedError):
            check_path_allowed("/etc/passwd", ALLOWED)
        connect.assert_not_called()


def test_allowlist_alone_does_not_strip_shell_metachars():
    # normpath does not touch ';' — a metachar path PASSES the allowlist. The
    # real defense is shlex.quote at command-build time (see test below).
    inj = "/var/log/nginx/x;reboot"
    assert check_path_allowed(inj, ALLOWED) == inj


def test_get_log_file_shell_quotes_path():
    """A path with shell metachars must be quoted before reaching run_command."""
    from infra_mcp.config import VMConfig
    from infra_mcp.tools import ssh_tools

    vm = VMConfig(
        name="web", host="h", user="u", password="p",
        log_dirs=["/var/log/nginx"],
    )
    captured: dict = {}

    def fake_run(vm_cfg, cmd, audit_path):
        captured["cmd"] = cmd
        return "", 0

    with mock.patch.object(ssh_tools.runtime, "get_config") as get_cfg, \
         mock.patch.object(ssh_tools.runtime, "get_audit_path", return_value=Path("/tmp/a")), \
         mock.patch.object(ssh_tools.ssh, "run_command", side_effect=fake_run):
        get_cfg.return_value.find_vm.return_value = vm
        ssh_tools.get_log_file("web", "/var/log/nginx/x;reboot")

    # The injected ';reboot' must be neutralized inside single quotes — the whole
    # path is one argument to tail, not a second command.
    assert captured["cmd"] == "tail -n 50 '/var/log/nginx/x;reboot'"
