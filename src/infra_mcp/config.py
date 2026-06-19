"""Pydantic config models for infra-mcp.yaml + fail-fast load_config()."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from infra_mcp.errors import ConfigError

_NAME_PATTERN = r"^[a-z][a-z0-9-]*$"
_KEYRING_SENTINEL = "keyring"


class DatabaseConfig(BaseModel):
    """One PostgreSQL database, embedded under its VM."""

    name: str = Field(pattern=_NAME_PATTERN)
    db_name: str
    user: str
    password: str
    host: str = "localhost"
    port: int = 5432
    slow_query_ms: int = 1000

    # SSH coordinates of the parent VM, stamped at load time by VMConfig so DB
    # tools can open an ephemeral tunnel without a separate persistent connection.
    ssh_host: str | None = None
    ssh_user: str | None = None
    ssh_password: str | None = None
    ssh_key_path: Path | None = None
    ssh_known_hosts_file: Path | None = None

    @field_validator("slow_query_ms")
    @classmethod
    def _positive_threshold(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("slow_query_ms must be > 0")
        return v


class VMConfig(BaseModel):
    """One logical VM entry."""

    name: str = Field(pattern=_NAME_PATTERN)
    host: str
    user: str
    key_path: Path | None = None
    password: str | None = None
    known_hosts_file: Path | None = None

    @model_validator(mode="after")
    def _require_auth(self) -> "VMConfig":
        if self.key_path is None and self.password is None:
            raise ValueError(
                f"VM {self.name}: provide key_path or password for SSH auth"
            )
        return self
    services: list[str] = Field(default_factory=list)
    # Remote (Linux) directories — validated as POSIX absolute paths, NOT local
    # OS paths, so they work when the server runs on Windows/macOS.
    log_dirs: list[str] = Field(default_factory=list)
    databases: list[DatabaseConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _stamp_db_ssh(self) -> "VMConfig":
        for db in self.databases:
            db.ssh_host = self.host
            db.ssh_user = self.user
            db.ssh_password = self.password
            db.ssh_key_path = self.key_path
            db.ssh_known_hosts_file = self.known_hosts_file
        return self

    @field_validator("log_dirs")
    @classmethod
    def _abs_log_dirs(cls, v: list[str]) -> list[str]:
        for d in v:
            if not d.startswith("/"):
                raise ValueError(f"log_dirs entry must be an absolute path: {d}")
        return v


class InfraMcpConfig(BaseModel):
    """Top-level config file model."""

    vms: list[VMConfig] = Field(min_length=1)
    audit_log_path: Path = Path("~/.infra-mcp/audit.jsonl").expanduser()
    log_level: str = "WARNING"
    # In-memory schema cache freshness window. An entry older than this is
    # re-fetched on read. Cache lives only in the running process (no file).
    schema_cache_ttl_hours: int = 24

    @field_validator("schema_cache_ttl_hours")
    @classmethod
    def _positive_ttl(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("schema_cache_ttl_hours must be > 0")
        return v

    @model_validator(mode="after")
    def _unique_db_names(self) -> InfraMcpConfig:
        seen: set[str] = set()
        for vm in self.vms:
            for db in vm.databases:
                if db.name in seen:
                    raise ValueError(f"duplicate database name across VMs: {db.name}")
                seen.add(db.name)
        return self

    def find_vm(self, name: str) -> VMConfig | None:
        return next((vm for vm in self.vms if vm.name == name), None)

    def find_db(self, name: str) -> tuple[VMConfig, DatabaseConfig] | None:
        for vm in self.vms:
            for db in vm.databases:
                if db.name == name:
                    return vm, db
        return None


def _resolve_keyring_passwords(config: InfraMcpConfig) -> None:
    """Replace the `keyring` sentinel with the credential from the system keychain."""
    import keyring

    for vm in config.vms:
        for db in vm.databases:
            if db.password == _KEYRING_SENTINEL:
                secret = keyring.get_password("infra-mcp", db.name)
                if secret is None:
                    raise ConfigError(
                        f"database {db.name} uses keyring but no credential found "
                        f"under infra-mcp:{db.name}"
                    )
                db.password = secret


def default_config_path() -> Path:
    """Resolve config path from INFRA_MCP_CONFIG or the default location."""
    env = os.environ.get("INFRA_MCP_CONFIG")
    if env:
        return Path(env).expanduser()
    return Path("~/.infra-mcp/infra-mcp.yaml").expanduser()


def load_config(path: Path | None = None) -> InfraMcpConfig:
    """Load and validate config. Raises ConfigError on any failure (fail fast)."""
    cfg_path = Path(path) if path is not None else default_config_path()
    if not cfg_path.exists():
        raise ConfigError(f"config file not found: {cfg_path}")
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {cfg_path}: {e}") from e
    try:
        config = InfraMcpConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"config validation failed: {e}") from e
    config.audit_log_path = config.audit_log_path.expanduser()
    _resolve_keyring_passwords(config)
    return config
