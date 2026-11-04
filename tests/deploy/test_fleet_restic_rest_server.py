"""Deploy contract tests for DeskComputer restic rest-server hub (#4016)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "deploy" / "fleet-backup"
ACL = ROOT / "docs" / "tailscale" / "acl-fleet-backup.hujson"
RUNBOOK = ROOT / "docs" / "runbooks" / "fleet-restic-backup.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_rest_server_run_uses_append_only_private_repos_and_tailscale_bind() -> None:
    text = _read(BACKUP / "rest-server-run.sh")
    assert "--append-only" in text
    assert "--private-repos" in text
    assert "tailscale ip -4" in text
    assert '--listen "${TS_IP}:${RESTIC_REST_LISTEN_PORT}"' in text
    assert "RESTIC_REST_HTPASSWD_FILE" in text


def test_env_example_does_not_contain_secrets() -> None:
    text = _read(BACKUP / "rest-server.env.example")
    assert "RESTIC_PASSWORD" not in text
    assert "$2" not in text  # bcrypt htpasswd material
    assert "/etc/restic-rest-server/htpasswd" in text


def test_systemd_unit_requires_tailscale_and_env_file() -> None:
    text = _read(BACKUP / "systemd" / "restic-rest-server.service")
    assert "Requires=tailscaled.service" in text
    assert "EnvironmentFile=-@ENV_FILE@" in text
    assert "restic-rest-server-run.sh" not in text  # templated at install time


def test_install_script_does_not_embed_passwords() -> None:
    text = _read(BACKUP / "install-rest-server.sh")
    assert "htpasswd" in text
    assert "-B" not in text  # no auto-generated credentials
    assert "github.com/restic/rest-server/releases" in text


def test_acl_replaces_smb_445_with_rest_server_port() -> None:
    text = _read(ACL)
    assert "*:445" in text
    assert "REMOVE" in text or "legacy" in text.lower()
    assert "*:8000" in text
    assert "tag:fleet-member" in text
    assert "tag:fleet-hub" in text


def test_runbook_requires_acl_before_go_live() -> None:
    text = _read(RUNBOOK)
    assert "before go-live" in text.lower() or "BEFORE" in text
    assert "acl-fleet-backup.hujson" in text
    assert "445" in text
    assert "8000" in text


def test_clients_example_one_user_per_machine() -> None:
    text = _read(BACKUP / "clients.example.yml")
    users = re.findall(r"htpasswd_user:\s*(\w+)", text)
    assert len(users) == len(set(users))
    assert "oglaptop" in users
    assert "DeskComputer" in text
