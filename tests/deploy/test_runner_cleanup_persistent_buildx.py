"""Persistent shared buildx builders survive runner-cleanup (#1988).

UpstreamDrift #13208 moves to one long-lived ``docker-container`` builder per
host (``ud-buildkit``) whose state volume is the layer cache, bounded by
BuildKit's own GC. These tests drive the real ``cleanup_docker`` with a
stateful docker shim modelling that builder next to an ephemeral
``builder-abc`` (as docker/setup-buildx-action leaves behind) and prove:

* the ephemeral builder's container, cache, state volume and definition are
  still reclaimed exactly as before;
* the persistent builder's container, cache, state volume and definition are
  never pruned or removed, on both the routine and the aggressive path;
* an invalid ``PERSISTENT_BUILDX_BUILDERS`` entry fails loudly before any prune.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path

CLEANUP = Path(__file__).resolve().parents[2] / "deploy" / "runner-cleanup.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)

# Stateful docker stub: answers the queries cleanup_docker makes and records
# every invocation (one line of argv per call) in $DOCKER_CALLS.
DOCKER_SHIM = r"""#!/usr/bin/env bash
printf "%s\n" "$*" >> "$DOCKER_CALLS"
case "$1 $2" in
  "inspect --format")
    case "$4" in
      buildx_buildkit_ud-buildkit0) [[ -n "$UD_STATE" ]] && { echo "$UD_STATE"; exit 0; }; exit 1 ;;
      buildx_buildkit_builder-abc0) echo exited; exit 0 ;;
    esac
    exit 1 ;;
  "start buildx_buildkit_ud-buildkit0") exit "${START_RC:-0}" ;;
  "buildx inspect") printf "Name:          %s\nDriver:        docker-container\n" "$CURRENT_BUILDER"; exit 0 ;;
  "volume ls")
    printf "buildx_buildkit_ud-buildkit0_state\nbuildx_buildkit_builder-abc0_state\nuser_data\n"; exit 0 ;;
esac
exit 0
"""


def _run_cleanup(
    tmp_path: Path,
    *,
    aggressive: str = "1",
    ud_state: str = "running",
    current_builder: str = "builder-abc",
    start_rc: str = "0",
    persistent: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    assert BASH is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(DOCKER_SHIM, encoding="utf-8", newline="\n")
    docker.chmod(0o755)
    calls = tmp_path / "docker-calls.log"
    calls.touch()

    script = CLEANUP.read_text(encoding="utf-8").split("# Emit a textfile-collector metric", 1)[0]
    harness = tmp_path / "cleanup-docker.sh"
    harness.write_text(script + "\ncleanup_docker\n", encoding="utf-8", newline="\n")

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{as_bash_path(bin_dir)}:{env['PATH']}",
            "DOCKER_CALLS": as_bash_path(calls),
            "DOCKER_AGGRESSIVE": aggressive,
            "PRUNE_DOCKER_VOLUMES": "1",
            "UD_STATE": ud_state,
            "CURRENT_BUILDER": current_builder,
            "START_RC": start_rc,
            "LOG_DIR": as_bash_path(tmp_path / "logs"),
            "USER": "runner-test",
        }
    )
    env.pop("PERSISTENT_BUILDX_BUILDERS", None)
    if persistent is not None:
        env["PERSISTENT_BUILDX_BUILDERS"] = persistent
    result = subprocess.run([BASH, as_bash_path(harness)], capture_output=True, text=True, check=False, env=env)
    return result, calls.read_text(encoding="utf-8").splitlines()


def _destructive(calls: list[str]) -> list[str]:
    verbs = ("container prune", "buildx prune", "builder prune", "volume prune", "volume rm", "buildx rm", "rm ")
    return [c for c in calls if c.startswith(verbs)]


@pytest.mark.parametrize("aggressive", ["0", "1"])
def test_ephemeral_builder_reclaimed_persistent_builder_untouched(tmp_path: Path, aggressive: str) -> None:
    result, calls = _run_cleanup(tmp_path, aggressive=aggressive)

    assert result.returncode == 0, result.stderr
    destructive = _destructive(calls)
    assert not [c for c in destructive if "ud-buildkit" in c], f"persistent builder touched: {destructive}"
    assert "volume rm buildx_buildkit_builder-abc0_state" in calls
    assert "buildx rm builder-abc" in calls
    assert any(c.startswith("container prune --force") for c in calls)
    if aggressive == "1":
        assert "buildx prune --all --force --builder builder-abc" in calls


def test_persistent_current_builder_cache_is_not_pruned(tmp_path: Path) -> None:
    result, calls = _run_cleanup(tmp_path, current_builder="ud-buildkit")

    assert result.returncode == 0, result.stderr
    assert not [c for c in calls if c.startswith("buildx prune")]
    assert "builder prune --all --force" in calls, "default-builder cache is still reclaimed"


def test_stopped_persistent_builder_is_started_before_container_prune(tmp_path: Path) -> None:
    result, calls = _run_cleanup(tmp_path, ud_state="exited")

    assert result.returncode == 0, result.stderr
    start = calls.index("start buildx_buildkit_ud-buildkit0")
    prune = next(i for i, c in enumerate(calls) if c.startswith("container prune"))
    assert start < prune


def test_unstartable_persistent_builder_skips_container_prune_only(tmp_path: Path) -> None:
    result, calls = _run_cleanup(tmp_path, ud_state="exited", start_rc="1")

    assert result.returncode == 0, result.stderr
    assert not [c for c in calls if c.startswith("container prune")], "a stopped persistent builder must not be pruned"
    assert "volume rm buildx_buildkit_builder-abc0_state" in calls, "the rest of cleanup still runs"
    assert not [c for c in _destructive(calls) if "ud-buildkit" in c]


@pytest.mark.parametrize("bad", ["UD-Buildkit", "ud buildkit*", "-ud", "ud/buildkit"])
def test_invalid_allowlist_entry_fails_loudly_before_any_prune(tmp_path: Path, bad: str) -> None:
    result, calls = _run_cleanup(tmp_path, persistent=f"ud-buildkit {bad}")

    assert result.returncode == 2
    assert "invalid PERSISTENT_BUILDX_BUILDERS entry" in result.stderr
    assert _destructive(calls) == []


def test_empty_allowlist_protects_nothing(tmp_path: Path) -> None:
    result, calls = _run_cleanup(tmp_path, persistent="")

    assert result.returncode == 0, result.stderr
    reaped = next(c for c in calls if c.startswith("volume rm"))
    assert "buildx_buildkit_ud-buildkit0_state" in reaped
    assert "buildx_buildkit_builder-abc0_state" in reaped


def test_default_allowlist_is_ud_buildkit() -> None:
    text = CLEANUP.read_text(encoding="utf-8")
    assert 'PERSISTENT_BUILDX_BUILDERS="${PERSISTENT_BUILDX_BUILDERS-ud-buildkit}"' in text
