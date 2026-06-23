# tests/test_version.py
from typer.testing import CliRunner

import infra_mcp
from infra_mcp.cli import app

runner = CliRunner()


def test_version_is_real_string():
    # No longer the stale hardcoded "0.1.0"; resolved from package metadata.
    assert isinstance(infra_mcp.__version__, str)
    assert infra_mcp.__version__ not in ("", "0.1.0")


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert infra_mcp.__version__ in result.output


def test_version_command_prints_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert infra_mcp.__version__ in result.output
