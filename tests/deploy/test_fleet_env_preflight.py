"""CLI preflight for stale HUB_URL in secrets (#2058)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = REPO_ROOT / "deploy" / "fleet-env-preflight.py"


def test_preflight_rejects_stale_controltower_hub(tmp_path: Path) -> None:
    secrets = tmp_path / "env"
    secrets.write_text(
        "HUB_URL=http://controltower.tail2bbcc7.ts.net:8321\n",
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
    assert result.returncode == 1
    assert "disagrees with machine_registry" in result.stderr


def test_preflight_allows_override_env_in_secrets(tmp_path: Path) -> None:
    secrets = tmp_path / "env"
    secrets.write_text(
        "HUB_URL=http://controltower.tail2bbcc7.ts.net:8321\nHUB_URL_REGISTRY_OVERRIDE=1\n",
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
