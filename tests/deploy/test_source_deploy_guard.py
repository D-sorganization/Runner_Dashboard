"""Regression coverage for source deploys targeting artifact releases."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "deploy" / "source-deploy-guard.sh"
pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)


def _run_guard(target: Path) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run(
        [BASH, "-c", 'source "$1"; source_deploy_guard "$2"', "guard-test", as_bash_path(GUARD), as_bash_path(target)],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ.copy(),
    )


def test_refuses_symlink_into_versioned_release_before_any_copy(tmp_path: Path) -> None:
    releases = tmp_path / "actions-runners" / "dashboard-releases" / "verified-sha"
    releases.mkdir(parents=True)
    (releases / "index.html").write_text("artifact", encoding="utf-8")
    target = tmp_path / "actions-runners" / "dashboard"
    target.symlink_to(releases, target_is_directory=True)
    result = _run_guard(target)
    assert result.returncode != 0
    assert "artifact" in result.stderr.lower() or "versioned" in result.stderr.lower()
    assert (releases / "index.html").read_text(encoding="utf-8") == "artifact"


def test_refuses_artifact_metadata_in_direct_target(tmp_path: Path) -> None:
    target = tmp_path / "dashboard"
    target.mkdir()
    (target / "deployment.json").write_text(
        '{"compatibility":{"artifact_schema":"runner-dashboard-artifact-v2"}}', encoding="utf-8"
    )
    result = _run_guard(target)
    assert result.returncode != 0
    assert "artifact" in result.stderr.lower()


def test_allows_ordinary_source_deployment_directory(tmp_path: Path) -> None:
    target = tmp_path / "dashboard"
    target.mkdir()
    result = _run_guard(target)
    assert result.returncode == 0, result.stderr


def test_malformed_deployment_metadata_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "dashboard"
    target.mkdir()
    (target / "deployment.json").write_text("{broken", encoding="utf-8")
    result = _run_guard(target)
    assert result.returncode != 0
    assert "metadata" in result.stderr.lower() or "artifact" in result.stderr.lower()


def test_update_script_refuses_versioned_target_before_backup(tmp_path: Path) -> None:
    release = tmp_path / "dashboard-releases" / "release-a"
    release.mkdir(parents=True)
    sentinel = release / "asset.js"
    sentinel.write_text("verified artifact", encoding="utf-8")
    target = tmp_path / "dashboard"
    target.symlink_to(release, target_is_directory=True)
    assert BASH is not None
    result = subprocess.run(
        [
            BASH,
            str(ROOT / "deploy" / "update-deployed.sh"),
            "--repo",
            str(ROOT),
            "--deploy-dir",
            str(target),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "artifact" in result.stderr.lower() or "versioned" in result.stderr.lower()
    assert sentinel.read_text(encoding="utf-8") == "verified artifact"
    assert not list(tmp_path.glob("dashboard.bak.*"))


def test_setup_refuses_versioned_target_before_preflight(tmp_path: Path) -> None:
    release = tmp_path / "dashboard-releases" / "release-b"
    release.mkdir(parents=True)
    sentinel = release / "asset.js"
    sentinel.write_text("verified artifact", encoding="utf-8")
    target = tmp_path / "dashboard"
    target.symlink_to(release, target_is_directory=True)
    assert BASH is not None
    result = subprocess.run(
        [BASH, str(ROOT / "deploy" / "setup.sh"), "--deploy-dir", str(target), "--check-only"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "artifact" in result.stderr.lower() or "versioned" in result.stderr.lower()
    assert sentinel.read_text(encoding="utf-8") == "verified artifact"


def test_unexpected_metadata_shape_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "dashboard"
    target.mkdir()
    (target / "deployment.json").write_text("[]", encoding="utf-8")

    result = _run_guard(target)

    assert result.returncode != 0
    assert "invalid shape" in result.stderr.lower()
    assert "traceback" not in result.stderr.lower()
