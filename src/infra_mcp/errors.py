"""Error hierarchy. Every error renders to a short human-readable string for the agent."""


class InfraMcpError(Exception):
    """Base for all infra-mcp errors. Subclasses carry a short, safe message."""


class VMUnreachableError(InfraMcpError):
    """SSH connection failed or timed out."""


class CommandNotAllowedError(InfraMcpError):
    """Path or command fell outside its allowlist (raised before any network call)."""


class ReadOnlyViolationError(InfraMcpError):
    """A non-SELECT or multi-statement SQL reached query_db."""


class ConfigError(InfraMcpError):
    """infra-mcp.yaml failed validation or a referenced credential is missing."""


class QueryError(InfraMcpError):
    """A guarded SELECT reached the database but failed to execute (e.g. bad column)."""
