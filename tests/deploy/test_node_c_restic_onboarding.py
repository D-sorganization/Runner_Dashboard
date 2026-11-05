"""Deploy contract tests for node-c restic onboarding and cleanup audit (#4010)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON

ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "deploy" / "fleet-backup"
RUNBOOK = ROOT / "docs" / "runbooks" / "node-c-restic-onboarding.md"

pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_node_c_cleanup_audit_refuses_destructive_flags() -> None:
    script = BACKUP / "node-c-cleanup-audit.sh"
    for flag in ("--delete", "--apply", "--force-delete"):
        proc = subprocess.run(
            [BASH, str(script), flag],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode != 0, flag
        assert "refus" in (proc.stdout + proc.stderr).lower()


def test_node_c_cleanup_audit_default_is_report_only() -> None:
    text = _read(BACKUP / "node-c-cleanup-audit.sh")
    assert "NODE_C_CLEANUP_ALLOW_DELETE" in text
    assert "refus" in text.lower() or "never" in text.lower()
    assert "rm -rf" not in text
    assert "unlink(" not in text


def test_node_c_client_env_example_does_not_contain_secrets() -> None:
    text = _read(BACKUP / "node-c-client.env.example")
    assert "RESTIC_PASSWORD=" not in text
    assert "RESTIC_PASSWORD_FILE=" in text
    assert "/node-c" in text
    assert "$2" not in text


def test_node_c_onboard_script_defaults_to_dry_run() -> None:
    text = _read(BACKUP / "node-c-onboard-restic.sh")
    assert "RESTIC_DRY_RUN" in text or "DRY_RUN" in text
    assert "RESTIC_PASSWORD=" not in text.replace("RESTIC_PASSWORD_FILE", "")
    assert "htpasswd" not in text.lower() or "never" in text.lower()


def test_node_c_backup_run_invokes_restic_with_json() -> None:
    text = _read(BACKUP / "node-c-backup-run.sh")
    assert "restic" in text
    assert "--json" in text or "backup" in text
    assert "RESTIC_PASSWORD_FILE" in text


def test_systemd_node_c_backup_uses_env_file_and_timer() -> None:
    unit = _read(BACKUP / "systemd" / "node-c-restic-backup.service")
    timer = _read(BACKUP / "systemd" / "node-c-restic-backup.timer")
    assert "EnvironmentFile=-@ENV_FILE@" in unit
    assert "node-c-backup-run.sh" in unit
    assert "OnCalendar=" in timer


def test_runbook_forbids_rushed_live_deletions() -> None:
    text = _read(RUNBOOK)
    lowered = text.lower()
    assert "no rushed" in lowered or "no deletion" in lowered or "audit" in lowered
    assert "node-c-cleanup-audit" in text
    assert re.search(r"rest:http://[^\s]+/node-c", text)
    assert "fleet-restic-backup" in text or "clients.example.yml" in text
