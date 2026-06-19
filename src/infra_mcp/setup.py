"""First-run setup: create read-only PG roles, and starter-config generation.

Admin credentials are read via getpass and used ephemerally over SSH. They are
never written to disk, printed, or sent to the audit log.
"""

from __future__ import annotations

import getpass
import os
from pathlib import Path

import paramiko

from infra_mcp.config import InfraMcpConfig, VMConfig
from infra_mcp.errors import VMUnreachableError
from infra_mcp.ssh import CONNECT_TIMEOUT


def _open(vm: VMConfig) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    if vm.known_hosts_file is not None:
        client.load_host_keys(str(vm.known_hosts_file.expanduser()))
    else:
        client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    kwargs: dict = dict(hostname=vm.host, username=vm.user, timeout=CONNECT_TIMEOUT)
    if vm.key_path is not None:
        kwargs["key_filename"] = str(vm.key_path.expanduser())
    if vm.password is not None:
        kwargs["password"] = vm.password
        kwargs["look_for_keys"] = False
        kwargs["allow_agent"] = False
    try:
        client.connect(**kwargs)
    except Exception as e:
        raise VMUnreachableError(f"VM {vm.name} unreachable: {e}") from e
    return client


def _exec(client: paramiko.SSHClient, cmd: str) -> tuple[str, int]:
    """Run a command WITHOUT auditing — used only for setup; never logs creds.

    Returns (stdout+stderr, exit_code) so psql errors (which go to stderr) surface.
    """
    _, stdout, stderr = client.exec_command(cmd, timeout=CONNECT_TIMEOUT)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    combined = (out + err).strip()
    return combined, code


def _ro_role_sql(ro_user: str, ro_password: str, db_name: str) -> str:
    """Idempotent SQL: create role if absent, then (re)grant read-only privileges."""
    return (
        "DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ro_user}') THEN "
        f"CREATE ROLE {ro_user} LOGIN PASSWORD '{ro_password}'; "
        "END IF; END $$; "
        # Always (re)set login + password so the role is deterministic even if it
        # already existed with a different/no password.
        f"ALTER ROLE {ro_user} WITH LOGIN PASSWORD '{ro_password}'; "
        f"GRANT CONNECT ON DATABASE {db_name} TO {ro_user}; "
        f"GRANT USAGE ON SCHEMA public TO {ro_user}; "
        f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ro_user}; "
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT ON TABLES TO {ro_user};"
    )


def run_setup(config: InfraMcpConfig) -> list[tuple[str, bool, str]]:
    """For each VM: detect PostgreSQL, create the read-only role on each DB.

    Returns a list of (vm_name, success, message). Idempotent — safe to re-run.
    Prompts once per VM-with-databases for admin credentials via getpass.
    """
    results: list[tuple[str, bool, str]] = []
    for vm in config.vms:
        try:
            client = _open(vm)
        except VMUnreachableError as e:
            results.append((vm.name, False, str(e)))
            continue
        try:
            _, code = _exec(client, "which psql")
            if code != 0:
                results.append((vm.name, True, "no PostgreSQL (psql not found); skipped"))
                continue
            if not vm.databases:
                results.append((vm.name, True, "PostgreSQL present; no databases configured"))
                continue

            # Empty username => peer auth via `sudo -u postgres` (no password,
            # works when SSH user is root). Otherwise TCP auth with PGPASSWORD.
            admin_user = input(
                f"[{vm.name}] PostgreSQL admin username "
                "(vide = sudo -u postgres, auth peer): "
            ).strip()
            admin_pw = (
                getpass.getpass(f"[{vm.name}] PostgreSQL admin password: ")
                if admin_user
                else None
            )

            ok_dbs: list[str] = []
            for db in vm.databases:
                sql = _ro_role_sql(db.user, db.password, db.db_name)
                # SQL is fed via a QUOTED heredoc (<<'EOSQL') so the remote shell
                # does not expand Postgres dollar-quoting ($$) into its PID.
                if admin_user:
                    # PGPASSWORD inlined for ephemeral use only; never audited/printed.
                    cmd = (
                        f"PGPASSWORD='{admin_pw}' psql -h {db.host} -p {db.port} "
                        f"-U {admin_user} -d {db.db_name} -v ON_ERROR_STOP=1 "
                        f"<<'EOSQL'\n{sql}\nEOSQL\n"
                    )
                else:
                    cmd = (
                        f"sudo -u postgres psql -d {db.db_name} -v ON_ERROR_STOP=1 "
                        f"<<'EOSQL'\n{sql}\nEOSQL\n"
                    )
                out, rc = _exec(client, cmd)
                if rc == 0:
                    ok_dbs.append(db.name)
                else:
                    results.append(
                        (vm.name, False, f"role setup failed for {db.name}: {out.strip()}")
                    )
            if ok_dbs:
                results.append(
                    (vm.name, True, f"read-only role ready for: {', '.join(ok_dbs)}")
                )
        finally:
            client.close()
    return results


def _ssh_config_hosts() -> list[dict]:
    """Parse ~/.ssh/config for concrete host entries (skips wildcard patterns)."""
    cfg_path = Path("~/.ssh/config").expanduser()
    if not cfg_path.exists():
        return []
    ssh_cfg = paramiko.SSHConfig()
    with cfg_path.open(encoding="utf-8") as f:
        ssh_cfg.parse(f)
    hosts = []
    for host in ssh_cfg.get_hostnames():
        if "*" in host or "?" in host or host == "*":
            continue
        opts = ssh_cfg.lookup(host)
        hosts.append(
            {
                "alias": host,
                "hostname": opts.get("hostname", host),
                "user": opts.get("user", os.environ.get("USER", "root")),
                "key": opts.get("identityfile", ["~/.ssh/id_rsa"])[0],
            }
        )
    return hosts


# System units to drop from the discovered services allowlist (kept readable but
# not useful as app targets). Glob-prefix matches handled separately.
_SKIP_SERVICES = {
    "auditd", "chronyd", "crond", "dbus", "firewalld", "gssproxy", "irqbalance",
    "NetworkManager", "polkit", "rhsmcertd", "rpcbind", "rsyslog", "sshd",
    "tuned", "waagent", "xagt", "hypervkvpd", "hypervvssd", "gssproxy",
    "rngd", "atd", "lvm2-monitor", "dm-event", "mcelog",
}
_SKIP_PREFIXES = ("getty@", "serial-getty@", "user@", "systemd-", "user-runtime-dir@")

# Candidate log directories probed on each VM; only existing ones are kept.
_LOG_CANDIDATES = [
    "/opt/*/logs", "/opt/*/log",
    "/var/log/aerowebb", "/var/log/haproxy", "/var/log/httpd", "/var/log/tomcat",
    "/var/log/karaf", "/var/log/rabbitmq", "/var/log/keycloak", "/var/log/postgresql",
]


def _is_app_service(unit: str) -> bool:
    if unit in _SKIP_SERVICES:
        return False
    return not any(unit.startswith(p) for p in _SKIP_PREFIXES)


def _discover_services(client: paramiko.SSHClient) -> list[str]:
    out, code = _exec(
        client,
        "systemctl list-units --type=service --state=running --no-legend --no-pager",
    )
    if code != 0:
        return []
    services = []
    for ln in out.splitlines():
        parts = ln.split()
        if not parts:
            continue
        unit = parts[0]
        if unit.endswith(".service"):
            name = unit[: -len(".service")]
            if _is_app_service(name):
                services.append(name)
    return services


def _discover_log_dirs(client: paramiko.SSHClient) -> list[str]:
    out, _ = _exec(client, f"ls -d {' '.join(_LOG_CANDIDATES)} 2>/dev/null")
    return [d.strip() for d in out.splitlines() if d.strip()]


def _discover_databases(client: paramiko.SSHClient) -> list[str]:
    _, code = _exec(client, "command -v psql >/dev/null 2>&1")
    if code != 0:
        return []
    out, code = _exec(
        client,
        "sudo -u postgres psql -Atc \"SELECT datname FROM pg_database "
        "WHERE NOT datistemplate AND datname <> 'postgres'\" 2>/dev/null",
    )
    if code != 0:
        return []
    return [d.strip() for d in out.splitlines() if d.strip()]


def discover_config(config: InfraMcpConfig) -> str:
    """Connect to each VM in the config and emit an enriched YAML with the REAL
    running services, existing log directories, and detected databases.

    Host / user / password / known_hosts are preserved from the input config.
    The read-only DB password is reused from the existing config if present,
    else a CHANGEME placeholder is written.
    """
    # Reuse an existing RO password if the config already defines one.
    ro_pw = "CHANGEME"
    for vm in config.vms:
        for db in vm.databases:
            if db.password and db.password != "CHANGEME":
                ro_pw = db.password
                break

    lines = [
        "# Auto-discovered by `infra-mcp discover`. Review before use.",
        f"audit_log_path: {config.audit_log_path}",
        "vms:",
    ]
    for vm in config.vms:
        services: list[str] = []
        log_dirs: list[str] = []
        dbs: list[str] = []
        reachable = True
        try:
            client = _open(vm)
            try:
                services = _discover_services(client)
                log_dirs = _discover_log_dirs(client)
                dbs = _discover_databases(client)
            finally:
                client.close()
        except VMUnreachableError:
            reachable = False

        lines.append(f"  - name: {vm.name}")
        lines.append(f"    host: {vm.host}")
        lines.append(f"    user: {vm.user}")
        if vm.password is not None:
            lines.append(f'    password: "{vm.password}"')
        if vm.key_path is not None:
            lines.append(f"    key_path: {vm.key_path}")
        if not reachable:
            lines.append("    # ⚠️ unreachable during discovery — values below not refreshed")
        if services:
            lines.append("    services:")
            lines.extend(f"      - {s}" for s in services)
        else:
            lines.append("    services: []")
        if log_dirs:
            lines.append("    log_dirs:")
            lines.extend(f"      - {d}" for d in log_dirs)
        else:
            lines.append("    log_dirs: []")
        if dbs:
            lines.append("    databases:")
            for dbname in dbs:
                logical = _normalize_name(f"{vm.name}-{dbname}")
                lines.append(f"      - name: {logical}")
                lines.append(f"        db_name: {dbname}")
                lines.append("        user: infra_readonly")
                lines.append(f'        password: "{ro_pw}"')
                lines.append("        host: localhost   # via tunnel SSH")
                lines.append("        port: 5432")
    return "\n".join(lines) + "\n"


def _normalize_name(alias: str) -> str:
    name = "".join(c if (c.isalnum() or c == "-") else "-" for c in alias.lower())
    name = name.strip("-") or "vm"
    if not name[0].isalpha():
        name = "vm-" + name
    return name


def generate_config() -> str:
    """Discover hosts from ~/.ssh/config, list running services, emit starter YAML."""
    hosts = _ssh_config_hosts()
    lines = ["# Auto-generated by `infra-mcp generate-config`. Review before use.", "vms:"]
    if not hosts:
        lines.append("  # No hosts found in ~/.ssh/config")
        return "\n".join(lines) + "\n"

    for h in hosts:
        services: list[str] = []
        vm = VMConfig(
            name=_normalize_name(h["alias"]),
            host=h["hostname"],
            user=h["user"],
            key_path=Path(h["key"]),
        )
        try:
            client = _open(vm)
            try:
                out, code = _exec(
                    client,
                    "systemctl list-units --type=service --state=running "
                    "--no-legend --no-pager",
                )
                if code == 0:
                    for ln in out.splitlines():
                        unit = ln.split()[0] if ln.split() else ""
                        if unit.endswith(".service"):
                            services.append(unit[: -len(".service")])
            finally:
                client.close()
        except VMUnreachableError:
            services = []

        lines.append(f"  - name: {vm.name}")
        lines.append(f"    host: {vm.host}")
        lines.append(f"    user: {vm.user}")
        lines.append(f"    key_path: {h['key']}")
        if services:
            lines.append("    services:")
            lines.extend(f"      - {s}" for s in services)
        else:
            lines.append("    services: []  # unreachable or none running")
    return "\n".join(lines) + "\n"
