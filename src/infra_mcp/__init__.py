"""infra-mcp: read-only MCP server for on-prem VM and PostgreSQL diagnosis."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("infra-mcp")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"
