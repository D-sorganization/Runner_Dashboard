"""WP23 H-09: deploy rollback backup discovery and strict CLI flags (#4277)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
LIB = DEPLOY / "lib.sh"
ROLLBACK = DEPLOY / "rollback.sh"
ROLLBACK_DEPLOYED = DEPLOY / "rollback-deployed.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)


def _bash(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    merged = {**dict(**__import__("os").environ), **(env or {})}
    return subprocess.run([BASH, *args], capture_output=True, text=True, env=merged)


def _source_lib_snippet(deploy_dir: Path) -> str:
    lib = as_bash_path(LIB)
    deploy = as_bash_path(deploy_dir)
    return f"""
set -euo pipefail
# shellcheck source=deploy/lib.sh
source "{lib}"
latest_deploy_backup "{deploy}"
"""


def test_latest_deploy_backup_matches_backup_dir_writes(tmp_path: Path) -> None:
    deploy_dir = tmp_path / "actions-runners" / "dashboard"
    deploy_dir.mkdir(parents=True)
    older = tmp_path / "actions-runners" / "dashboard.bak.20200101_000000"
    newer = tmp_path / "actions-runners" / "dashboard.bak.20200202_000000"
    older.mkdir()
    newer.mkdir()

    res = _bash("-c", _source_lib_snippet(deploy_dir))
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == as_bash_path(newer)


def test_latest_deploy_backup_ignores_unrelated_sibling_dirs(tmp_path: Path) -> None:
    deploy_dir = tmp_path / "fleet" / "dashboard"
    deploy_dir.mkdir(parents=True)
    backup = tmp_path / "fleet" / "dashboard.bak.20250303_120000"
    backup.mkdir()
    (tmp_path / "fleet" / "dashboard.other").mkdir()

    res = _bash("-c", _source_lib_snippet(deploy_dir))
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == as_bash_path(backup)


def test_rollback_sh_unknown_flag_exits_2(tmp_path: Path) -> None:
    deploy_dir = tmp_path / "dashboard"
    deploy_dir.mkdir()
    res = _bash(
        as_bash_path(ROLLBACK),
        "--not-a-real-flag",
        env={"DEPLOY_DIR": as_bash_path(deploy_dir)},
    )
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "Unknown" in res.stderr or "unknown" in res.stderr.lower()


def test_rollback_sh_list_finds_backup_dir_style_snapshots(tmp_path: Path) -> None:
    deploy_dir = tmp_path / "dashboard"
    deploy_dir.mkdir()
    backup = tmp_path / "dashboard.bak.20200404_000000"
    backup.mkdir()
    (backup / "VERSION").write_text("test-sha", encoding="utf-8")

    res = _bash(
        as_bash_path(ROLLBACK),
        "--list",
        env={"DEPLOY_DIR": as_bash_path(deploy_dir)},
    )
    assert res.returncode == 0, res.stderr
    assert "20200404_000000" in res.stdout


def test_rollback_deployed_default_backup_uses_sibling_bak_dirs() -> None:
    content = ROLLBACK_DEPLOYED.read_text(encoding="utf-8")
    assert "latest_deploy_backup" in content
    assert "ls -1dt /var/backups/runner-dashboard" not in content


def test_rollback_sh_does_not_downgrade_db_on_dry_run() -> None:
    content = ROLLBACK.read_text(encoding="utf-8")
    assert 'SKIP_DB_MIGRATION" != "true" && "$DRY_RUN" != "true"' in content
