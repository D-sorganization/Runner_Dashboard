"""CLI preflight for stale HUB_URL in secrets (#2058)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = REPO_ROOT / "deploy" / "fleet-env-preflight.py"


def _repo_root_with_synthetic_registry(tmp_path: Path, registry_yaml: str) -> Path:
    """Build a repo-root fixture: the real backend/ via symlinks, except a
    synthetic machine_registry.yml (the shipped one carries no live topology, #3082).
    """
    work_backend = tmp_path / "work-repo" / "backend"
    work_backend.mkdir(parents=True)
    for entry in (REPO_ROOT / "backend").iterdir():
        if entry.name != "machine_registry.yml":
            (work_backend / entry.name).symlink_to(entry)
    (work_backend / "machine_registry.yml").write_text(registry_yaml, encoding="utf-8")
    return work_backend.parent


_SYNTHETIC_HUB_REGISTRY = """\
version: 1
machines:
  - name: hub-a
    role: hub
    dashboard_url: http://hub-a.fleet-example.ts.net:8321
"""


def test_preflight_rejects_stale_node_a_hub(tmp_path: Path) -> None:
    secrets = tmp_path / "env"
    secrets.write_text(
        "HUB_URL=http://node-a.fleet-example.ts.net:8321\n",
        encoding="utf-8",
    )
    repo_root = _repo_root_with_synthetic_registry(tmp_path, _SYNTHETIC_HUB_REGISTRY)
    result = subprocess.run(
        [
            sys.executable,
            str(PREFLIGHT),
            "--repo-root",
            str(repo_root),
            "--secrets-file",
            str(secrets),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 1
    assert "disagrees with machine_registry" in result.stderr


def test_preflight_allows_override_env_in_secrets(tmp_path: Path) -> None:
    secrets = tmp_path / "env"
    secrets.write_text(
        "HUB_URL=http://node-a.fleet-example.ts.net:8321\nHUB_URL_REGISTRY_OVERRIDE=1\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(PREFLIGHT),
            "--repo-root",
            str(REPO_ROOT),
            "--secrets-file",
            str(secrets),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
