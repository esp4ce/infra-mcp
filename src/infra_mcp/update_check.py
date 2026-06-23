"""Best-effort PyPI update notification.

Checks PyPI for a newer infra-mcp release and prints an upgrade hint to stderr.
Never blocks startup and never raises: any failure is swallowed. Disable by
setting INFRA_MCP_NO_UPDATE_CHECK to any non-empty value.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import TextIO

from infra_mcp import __version__

_PYPI_URL = "https://pypi.org/pypi/infra-mcp/json"
_CACHE_TTL_SECONDS = 24 * 60 * 60
_DISABLE_ENV = "INFRA_MCP_NO_UPDATE_CHECK"


def _cache_path() -> Path:
    return Path("~/.infra-mcp/.update-check.json").expanduser()


def _disabled() -> bool:
    return bool(os.environ.get(_DISABLE_ENV))


def _read_cache(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_cache(path: Path, latest_version: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"last_check": time.time(), "latest_version": latest_version}),
            encoding="utf-8",
        )
    except OSError:
        pass


def _fetch_latest_version(timeout: float = 2.0) -> str | None:
    try:
        with urllib.request.urlopen(_PYPI_URL, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        latest = payload["info"]["version"]
        return latest if isinstance(latest, str) else None
    except Exception:
        return None


def _is_newer(latest: str, current: str) -> bool:
    try:
        from packaging.version import InvalidVersion, parse

        try:
            return parse(latest) > parse(current)
        except InvalidVersion:
            return False
    except ModuleNotFoundError:
        def _ints(v: str) -> tuple[int, ...]:
            parts: list[int] = []
            for chunk in v.split("+")[0].split("-")[0].split("."):
                if not chunk.isdigit():
                    break
                parts.append(int(chunk))
            return tuple(parts)

        return _ints(latest) > _ints(current)


def _refresh_in_background() -> None:
    def _worker() -> None:
        latest = _fetch_latest_version()
        if latest:
            _write_cache(_cache_path(), latest)

    threading.Thread(target=_worker, daemon=True).start()


def _print_notice(latest: str, stream: TextIO) -> None:
    print(
        f"[infra-mcp] A new release is available: {__version__} → {latest}\n"
        f"Update with: uv tool upgrade infra-mcp  (or: pip install --upgrade infra-mcp)\n"
        f"Set {_DISABLE_ENV}=1 to disable this check.",
        file=stream,
    )


def maybe_notify(stream: TextIO | None = None) -> None:
    """Print an upgrade notice if a newer release is cached; refresh cache if stale.

    Best-effort and non-blocking: never raises, never delays the caller, never
    writes to stdout.
    """
    if stream is None:
        stream = sys.stderr
    try:
        if _disabled():
            return
        cache = _read_cache(_cache_path())
        if cache:
            latest = cache.get("latest_version")
            if isinstance(latest, str) and _is_newer(latest, __version__):
                _print_notice(latest, stream)
        last_check = cache.get("last_check", 0) if cache else 0
        if not isinstance(last_check, (int, float)):
            last_check = 0
        if time.time() - last_check > _CACHE_TTL_SECONDS:
            _refresh_in_background()
    except Exception:
        pass
