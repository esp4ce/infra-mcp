# tests/test_update_check_cli.py
import json
import time

import pytest
from typer.testing import CliRunner

from infra_mcp import update_check
from infra_mcp.cli import app

runner = CliRunner()


@pytest.fixture
def newer_cache(tmp_path, monkeypatch):
    path = tmp_path / ".update-check.json"
    monkeypatch.setattr(update_check, "_cache_path", lambda: path)
    monkeypatch.setattr(update_check, "__version__", "0.1.2")
    monkeypatch.setattr(update_check, "_refresh_in_background", lambda: None)
    path.write_text(
        json.dumps({"last_check": time.time(), "latest_version": "0.1.3"}),
        encoding="utf-8",
    )
    return path


def test_callback_emits_notice(newer_cache):
    # The `version` command is enough to exercise the root callback.
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "new release is available" in result.output


def test_version_flag_skips_check(newer_cache):
    # --version is eager and exits before the callback body runs.
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "new release is available" not in result.output
