import pytest

from infra_mcp import db
from infra_mcp.config import DatabaseConfig
from infra_mcp.errors import QueryError, VMUnreachableError


def _dbcfg() -> DatabaseConfig:
    return DatabaseConfig(
        name="d", db_name="app", user="ro", password="p",
        ssh_host="h", ssh_user="root", ssh_password="s",
    )


class _FakeTunnel:
    instances = []

    def __init__(self, *args, **kwargs):
        self.local_bind_port = 54321
        self.events = []
        _FakeTunnel.instances.append(self)

    def start(self):
        self.events.append("start")

    def stop(self):
        self.events.append("stop")


def _patch(monkeypatch, conn):
    _FakeTunnel.instances = []
    monkeypatch.setattr(db, "_verify_host_key", lambda db_cfg: None)
    monkeypatch.setattr(db, "SSHTunnelForwarder", _FakeTunnel)
    monkeypatch.setattr(db.psycopg2, "connect", lambda **k: conn)


def test_session_closes_conn_then_tunnel(monkeypatch):
    order = []

    class Conn:
        def set_session(self, **k):
            pass

        def close(self):
            order.append("conn_close")

    conn = Conn()
    _patch(monkeypatch, conn)
    with db._session(_dbcfg()) as c:
        assert c is conn
    tunnel = _FakeTunnel.instances[0]
    assert order == ["conn_close"]
    assert tunnel.events == ["start", "stop"]


def test_session_stops_tunnel_on_query_error(monkeypatch):
    closed = []

    class Conn:
        def set_session(self, **k):
            pass

        def close(self):
            closed.append(True)

    conn = Conn()
    _patch(monkeypatch, conn)
    with pytest.raises(RuntimeError):
        with db._session(_dbcfg()):
            raise RuntimeError("boom")
    tunnel = _FakeTunnel.instances[0]
    assert closed == [True]
    assert tunnel.events[-1] == "stop"


def test_session_wraps_tunnel_failure(monkeypatch):
    class BoomTunnel(_FakeTunnel):
        def start(self):
            raise OSError("no route to host")

    _FakeTunnel.instances = []
    monkeypatch.setattr(db, "_verify_host_key", lambda db_cfg: None)
    monkeypatch.setattr(db, "SSHTunnelForwarder", BoomTunnel)
    with pytest.raises(VMUnreachableError):
        with db._session(_dbcfg()):
            pass


def test_session_closes_conn_when_set_session_fails(monkeypatch):
    closed = []

    class Conn:
        def set_session(self, **k):
            raise db.psycopg2.OperationalError("readonly refused")

        def close(self):
            closed.append(True)

    _patch(monkeypatch, Conn())
    with pytest.raises(VMUnreachableError):
        with db._session(_dbcfg()):
            pass
    tunnel = _FakeTunnel.instances[0]
    assert closed == [True]          # connection was closed despite the failure
    assert tunnel.events[-1] == "stop"  # tunnel was torn down too


def test_session_verifies_host_key_before_tunnel(monkeypatch):
    calls = []
    monkeypatch.setattr(
        db, "_verify_host_key",
        lambda db_cfg: (_ for _ in ()).throw(VMUnreachableError("unknown host key")),
    )

    class _Boom(_FakeTunnel):
        def __init__(self, *a, **k):
            calls.append("tunnel_built")
            super().__init__(*a, **k)

    monkeypatch.setattr(db, "SSHTunnelForwarder", _Boom)
    with pytest.raises(VMUnreachableError):
        with db._session(_dbcfg()):
            pass
    assert calls == []  # tunnel never built — host-key check ran first and rejected


def test_run_select_wraps_execution_error_and_tears_down(monkeypatch):
    """A guarded SELECT that fails at execution (e.g. bad column) becomes a
    QueryError (caught by query_db → clean ERROR), and conn/tunnel are torn down."""
    closed = []

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql):
            raise db.psycopg2.errors.UndefinedColumn('column "x" does not exist')

    class Conn:
        def set_session(self, **k):
            pass

        def cursor(self):
            return Cur()

        def rollback(self):
            pass

        def close(self):
            closed.append(True)

    _patch(monkeypatch, Conn())
    with pytest.raises(QueryError):
        db.run_select(_dbcfg(), "SELECT x FROM t", 5, audit_log_path=None)
    tunnel = _FakeTunnel.instances[0]
    assert closed == [True]
    assert tunnel.events[-1] == "stop"
