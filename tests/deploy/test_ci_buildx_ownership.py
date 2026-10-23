"""Concurrent jobs must not delete each other's live Buildx builders."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml
from bash_host import BASH, SKIP_REASON, as_bash_path, docker_shim_prelude

ROOT = Path(__file__).resolve().parents[2]
RECOVER = ROOT / "scripts/ci/docker_buildx_recover.sh"
pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)
SHIM = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$DOCKER_CALLS"
if [ "$1 $2" = 'buildx ls' ]; then
  printf '%s\\n' builder-other-live gm-ci-other-live ud-buildkit rd-ci-999-1 rd-ci-101-2
fi
exit 0
"""


def _recover(
    tmp_path: Path, *, builder: str = "rd-ci-101-2", run_id: str = "101", recreate: bool = False
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    assert BASH is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(SHIM, newline="\n")
    docker.chmod(0o755)
    calls = tmp_path / "calls"
    calls.touch()
    harness = tmp_path / "recover.sh"
    harness.write_text(docker_shim_prelude(bin_dir) + RECOVER.read_text(), newline="\n")
    env = {
        **os.environ,
        "DOCKER_HOST": "unix:///nonexistent/buildx-test.sock",
        "DOCKER_CALLS": as_bash_path(calls),
        "BUILDX_BUILDER": builder,
        "GITHUB_RUN_ID": run_id,
        "GITHUB_RUN_ATTEMPT": "2",
    }
    args = [BASH, as_bash_path(harness)] + (["--recreate"] if recreate else [])
    result = subprocess.run(args, env=env, text=True, capture_output=True, check=False)
    return result, calls.read_text().splitlines()


def test_recovery_removes_only_its_exact_job_builder(tmp_path: Path) -> None:
    result, calls = _recover(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "buildx rm -f rd-ci-101-2" in calls
    destructive = [call for call in calls if " rm " in call or "prune" in call]
    assert destructive == ["buildx rm -f rd-ci-101-2"]


def test_retry_recreates_the_owned_builder(tmp_path: Path) -> None:
    result, calls = _recover(tmp_path, recreate=True)
    assert result.returncode == 0, result.stderr
    assert "buildx create --name rd-ci-101-2 --driver docker-container --use --bootstrap" in calls
    assert not any("other-live" in call or "ud-buildkit" in call or "rd-ci-999-1" in call for call in calls)


@pytest.mark.parametrize("builder,run_id", [("builder-other-live", "101"), ("rd-ci-101-2", "bad/id")])
def test_unproven_ownership_never_mutates_docker(tmp_path: Path, builder: str, run_id: str) -> None:
    _, calls = _recover(tmp_path, builder=builder, run_id=run_id)
    assert not any(" rm " in call or "prune" in call or "create" in call for call in calls)


def test_workflow_isolates_config_and_preserves_owned_name_on_retry() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/docker-build.yml").read_text())
    job = workflow["jobs"]["docker-image"]
    assert "github.run_attempt" in job["env"]["BUILDX_BUILDER"]
    prepare = next(step for step in job["steps"] if step.get("id") == "docker-config")
    assert "RUNNER_TEMP" in prepare["run"] and "GITHUB_RUN_ID" in prepare["run"]
    assert "DOCKER_CONFIG=%s" in prepare["run"] and "GITHUB_ENV" in prepare["run"]
    setup = next(step for step in job["steps"] if str(step.get("uses", "")).startswith("docker/setup-buildx-action@"))
    assert setup["with"]["name"] == "${{ env.BUILDX_BUILDER }}"
    build = next(step for step in job["steps"] if step.get("id") == "build")
    assert "docker_buildx_recover.sh --recreate" in build["run"]
