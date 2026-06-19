"""Process-wide config singleton, shared by server and tool modules.

Lives in its own module (importing only `config`) so tool modules can read the
config without importing `server` — which would create a circular import, since
`server` imports the tool modules to register them.
"""

from __future__ import annotations

from pathlib import Path

from infra_mcp.config import InfraMcpConfig, load_config

_config: InfraMcpConfig | None = None


def init_config(path: Path | None = None) -> InfraMcpConfig:
    """Load config into the singleton. Fail-fast on validation error."""
    global _config
    _config = load_config(path)
    return _config


def get_config() -> InfraMcpConfig:
    """Return the loaded config, loading the default on first access if needed."""
    if _config is None:
        return init_config()
    return _config


def get_audit_path() -> Path:
    return get_config().audit_log_path


def get_schema_cache_ttl() -> int:
    return get_config().schema_cache_ttl_hours
