"""Unit tests for the SSH service-command allowlist."""

from pathlib import Path
from unittest import mock

import pytest

from infra_mcp.config import VMConfig
from infra_mcp.errors import CommandNotAllowedError
from infra_mcp.ssh import check_service_allowed

VM = VMConfig(
    name="vm-test",
    host="192.168.1.10",
    user="deploy",
    key_path=Path("/home/deploy/.ssh/id_rsa"),
    services=["nginx", "haproxy"],
)


def test_allowed_service_passes():
    check_service_allowed(VM, "nginx")  # should not raise


def test_disallowed_service_rejected():
    with pytest.raises(CommandNotAllowedError):
        check_service_allowed(VM, "sshd")


def test_no_ssh_connection_when_service_rejected():
    with mock.patch("paramiko.SSHClient.connect") as connect:
        with pytest.raises(CommandNotAllowedError):
            check_service_allowed(VM, "sshd")
        connect.assert_not_called()