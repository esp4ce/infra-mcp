"""Append-only JSONL audit writer. One line per remote command."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    """ISO-8601 UTC timestamp, e.g. 2026-06-17T14:22:01Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_command(audit_log_path: Path, target: str, cmd: str, exit_code: int) -> None:
    """Append one audit entry. Creates the parent directory if needed."""
    audit_log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": _now_iso(), "target": target, "cmd": cmd, "exit_code": exit_code}
    with audit_log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")


def read_tail(audit_log_path: Path, lines: int) -> list[str]:
    """Return the last `lines` audit entries, oldest first. Empty if no log yet."""
    if not audit_log_path.exists():
        return []
    all_lines = audit_log_path.read_text(encoding="utf-8").splitlines()
    return [ln for ln in all_lines if ln.strip()][-lines:]
