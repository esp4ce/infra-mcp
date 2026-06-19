from pathlib import Path

from infra_mcp.config import load_config


def test_db_inherits_vm_ssh_creds(tmp_path: Path):
    yaml_text = """
vms:
  - name: vm1
    host: vm1.example.com
    user: root
    password: secret
    databases:
      - name: db1
        db_name: app
        user: ro
        password: ropw
"""
    p = tmp_path / "c.yaml"
    p.write_text(yaml_text)
    cfg = load_config(p)
    db = cfg.vms[0].databases[0]
    assert db.ssh_host == "vm1.example.com"
    assert db.ssh_user == "root"
    assert db.ssh_password == "secret"
    assert db.ssh_key_path is None


def test_db_inherits_vm_key_path(tmp_path: Path):
    yaml_text = """
vms:
  - name: vm2
    host: vm2.example.com
    user: deploy
    key_path: ~/.ssh/id_rsa
    databases:
      - name: db2
        db_name: app
        user: ro
        password: ropw
"""
    p = tmp_path / "c.yaml"
    p.write_text(yaml_text)
    cfg = load_config(p)
    db = cfg.vms[0].databases[0]
    assert db.ssh_host == "vm2.example.com"
    assert db.ssh_user == "deploy"
    assert str(db.ssh_key_path) == str(Path("~/.ssh/id_rsa"))
    assert db.ssh_password is None
