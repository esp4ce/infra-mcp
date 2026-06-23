"""Typer CLI: run / setup / generate-config / test subcommands."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

import typer

from infra_mcp import __version__
from infra_mcp.config import load_config
from infra_mcp.errors import InfraMcpError

app = typer.Typer(add_completion=False, help="Read-only infra diagnosis MCP server.")

_ConfigOpt = typer.Option(None, "--config", "-c", help="Path to infra-mcp.yaml")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show the installed version and exit.",
    ),
) -> None:
    """Read-only infra diagnosis MCP server."""


@app.command()
def version() -> None:
    """Print the installed infra-mcp version."""
    typer.echo(__version__)


@app.command()
def run(config: Optional[Path] = _ConfigOpt) -> None:
    """Start the stdio MCP server."""
    from infra_mcp import server

    try:
        server.init_config(config)
    except InfraMcpError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1) from e
    server.mcp.run(transport="stdio")


@app.command()
def setup(config: Optional[Path] = _ConfigOpt) -> None:
    """Scan VMs, detect PostgreSQL, and create read-only roles. Idempotent."""
    from infra_mcp import setup as setup_mod

    try:
        cfg = load_config(config)
    except InfraMcpError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1) from e
    results = setup_mod.run_setup(cfg)
    for name, ok, msg in results:
        mark = "OK " if ok else "FAIL"
        typer.echo(f"[{mark}] {name}: {msg}")
    if any(not ok for _, ok, _ in results):
        raise typer.Exit(1)


@app.command(name="generate-config")
def generate_config(
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write to this path instead of stdout"
    ),
) -> None:
    """Discover VMs from ~/.ssh/config and write a starter infra-mcp.yaml."""
    from infra_mcp import setup as setup_mod

    yaml_text = setup_mod.generate_config()
    if output is None:
        typer.echo(yaml_text)
    else:
        output.expanduser().parent.mkdir(parents=True, exist_ok=True)
        output.expanduser().write_text(yaml_text, encoding="utf-8")
        typer.echo(f"Wrote {output}")


@app.command()
def discover(
    config: Optional[Path] = _ConfigOpt,
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write enriched YAML here (default: stdout)"
    ),
    in_place: bool = typer.Option(
        False, "--in-place", "-i", help="Overwrite the loaded config file in place"
    ),
) -> None:
    """Connect to each configured VM and refresh services / log_dirs / databases."""
    from infra_mcp import setup as setup_mod

    try:
        cfg = load_config(config)
    except InfraMcpError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1) from e
    yaml_text = setup_mod.discover_config(cfg)
    target = config if (in_place and config) else output
    if target is None:
        typer.echo(yaml_text)
    else:
        target.expanduser().parent.mkdir(parents=True, exist_ok=True)
        target.expanduser().write_text(yaml_text, encoding="utf-8")
        typer.echo(f"Wrote {target}")


@app.command()
def test(config: Optional[Path] = _ConfigOpt) -> None:
    """Attempt an SSH reachability check against each configured VM."""
    from infra_mcp import ssh

    try:
        cfg = load_config(config)
    except InfraMcpError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1) from e
    any_unreachable = False
    for vm in cfg.vms:
        start = time.monotonic()
        reachable = ssh.is_reachable(vm)
        latency_ms = int((time.monotonic() - start) * 1000)
        if reachable:
            typer.echo(f"[OK ] {vm.name}: reachable ({latency_ms} ms)")
        else:
            any_unreachable = True
            typer.echo(f"[FAIL] {vm.name}: unreachable")
    if any_unreachable:
        raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(app())
