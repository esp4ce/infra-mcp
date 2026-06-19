"""FastMCP server instance and tool registration.

Config lives in `infra_mcp.runtime` so tool modules can read it without importing
this module (which would be circular). `init_config` / `get_config` are re-exported
here for backwards compatibility.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from infra_mcp.runtime import get_audit_path, get_config, init_config

__all__ = ["mcp", "init_config", "get_config", "get_audit_path", "register_all"]

mcp = FastMCP("infra-mcp")


def register_all() -> None:
    """Register every tool on the FastMCP instance."""
    from infra_mcp.tools import db_tools, meta_tools, ssh_tools

    ssh_tools.register(mcp)
    db_tools.register(mcp)
    meta_tools.register(mcp)


# Register at import so `mcp.run()` and MCP clients see the tools.
register_all()
