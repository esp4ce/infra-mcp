# tests/test_update_check.py
import json
import time

import pytest

from infra_mcp import update_check


@pytest.fixture
def cache_file(tmp_path, monkeypatch):
    path = tmp_path / ".update-check.json"
    monkeypatch.setattr(update_check, "_cache_path", lambda: path)
    return path


# --- _is_newer -------------------------------------------------------------


@pytest.mark.parametrize(
    "latest,current,expected",
    [
        ("0.1.3", "0.1.2", True),
        ("0.1.2", "0.1.2", False),
        ("0.1.1", "0.1.2", False),
        ("1.0.0", "0.9.9", True),
        ("0.2.0", "0.10.0", False),  # numeric, not lexical
        ("not-a-version", "0.1.2", False),
    ],
)
def test_is_newer(latest, current, expected):
    assert update_check._is_newer(latest, current) is expected


# --- cache IO --------------------------------------------------------------


def test_cache_roundtrip(cache_file):
    update_check._write_cache(cache_file, "0.1.3")
    data = update_check._read_cache(cache_file)
    assert data["latest_version"] == "0.1.3"
    assert isinstance(data["last_check"], (int, float))


def test_read_missing_cache_returns_none(cache_file):
    assert update_check._read_cache(cache_file) is None


def test_read_corrupt_cache_returns_none(cache_file):
    cache_file.write_text("{not json", encoding="utf-8")
    assert update_check._read_cache(cache_file) is None


# --- maybe_notify ----------------------------------------------------------


def _seed(cache_file, latest, age_seconds):
    cache_file.write_text(
        json.dumps({"last_check": time.time() - age_seconds, "latest_version": latest}),
        encoding="utf-8",
    )


def test_notifies_when_newer(cache_file, monkeypatch, capsys):
    monkeypatch.setattr(update_check, "__version__", "0.1.2")
    monkeypatch.setattr(update_check, "_refresh_in_background", lambda: None)
    _seed(cache_file, "0.1.3", age_seconds=10)
    update_check.maybe_notify()
    err = capsys.readouterr().err
    assert "new release is available" in err
    assert "0.1.3" in err


def test_silent_when_up_to_date(cache_file, monkeypatch, capsys):
    monkeypatch.setattr(update_check, "__version__", "0.1.3")
    monkeypatch.setattr(update_check, "_refresh_in_background", lambda: None)
    _seed(cache_file, "0.1.3", age_seconds=10)
    update_check.maybe_notify()
    assert capsys.readouterr().err == ""


def test_disabled_does_no_io(cache_file, monkeypatch, capsys):
    monkeypatch.setenv("INFRA_MCP_NO_UPDATE_CHECK", "1")
    called = []
    monkeypatch.setattr(update_check, "_read_cache", lambda p: called.append("read"))
    monkeypatch.setattr(update_check, "_refresh_in_background", lambda: called.append("net"))
    update_check.maybe_notify()
    assert called == []
    assert capsys.readouterr().err == ""


def test_refresh_when_stale(cache_file, monkeypatch):
    monkeypatch.setattr(update_check, "__version__", "0.1.2")
    refreshed = []
    monkeypatch.setattr(update_check, "_refresh_in_background", lambda: refreshed.append(True))
    _seed(cache_file, "0.1.2", age_seconds=update_check._CACHE_TTL_SECONDS + 1)
    update_check.maybe_notify()
    assert refreshed == [True]


def test_no_refresh_when_fresh(cache_file, monkeypatch):
    monkeypatch.setattr(update_check, "__version__", "0.1.2")
    refreshed = []
    monkeypatch.setattr(update_check, "_refresh_in_background", lambda: refreshed.append(True))
    _seed(cache_file, "0.1.2", age_seconds=10)
    update_check.maybe_notify()
    assert refreshed == []


def test_swallows_errors(cache_file, monkeypatch, capsys):
    def boom(_path):
        raise RuntimeError("boom")

    monkeypatch.setattr(update_check, "_read_cache", boom)
    monkeypatch.setattr(update_check, "_refresh_in_background", lambda: None)
    update_check.maybe_notify()  # must not raise
    assert capsys.readouterr().err == ""


# --- _fetch_latest_version -------------------------------------------------


def test_fetch_parses_payload(monkeypatch):
    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"info": {"version": "9.9.9"}}).encode("utf-8")

    monkeypatch.setattr(update_check.urllib.request, "urlopen", lambda *a, **k: FakeResp())
    assert update_check._fetch_latest_version() == "9.9.9"


def test_fetch_returns_none_on_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr(update_check.urllib.request, "urlopen", boom)
    assert update_check._fetch_latest_version() is None
