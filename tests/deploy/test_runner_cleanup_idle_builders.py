"""Leaked ephemeral buildx builders are reaped once idle (#2002).

``cleanup_orphan_buildx_state`` only reaps *dangling* state volumes. A leaked
``docker/setup-buildx-action`` builder (``builder-<uuid>``) or a ``gm-ci-*``
builder whose container restarted keeps referencing its volume, so it was never
reclaimed. ``reap_idle_builders`` removes such a builder's container, state
volume and definition when it is old enough (by *creation* time — a restart
resets StartedAt) and ``docker top`` shows no build process. The persistent
shared builder (``ud-buildkit``, #1988) is never touched.

Every test drives the real script through ``docker_shim_prelude`` so docker can
only ever resolve to the stub (the guard from #1990: a test once pruned a live
daemon).
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from bash_host import BASH, SKIP_REASON, as_bash_path, docker_shim_prelude

CLEANUP = Path(__file__).resolve().parents[2] / "deploy" / "runner-cleanup.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason=SKIP_REASON)

# Stateful docker stub backed by $BUILDERS_DB, one builder container per line:
#   <container>|<created RFC3339>|<space-separated process names, or STOPPED>
# Every invocation is recorded (argv on one line) in $DOCKER_CALLS.
DOCKER_SHIM = r"""#!/usr/bin/env bash
printf "%s\n" "$*" >> "$DOCKER_CALLS"
field() { awk -F'|' -v c="$1" -v f="$2" '$1 == c {print $f; found=1} END {exit !found}' "$BUILDERS_DB"; }
case "$1" in
  info) exit 0 ;;
  ps) awk -F'|' '{print $1}' "$BUILDERS_DB"; exit 0 ;;
  inspect)
    # docker inspect --format '{{.Created}}' <container>
    [[ "$3" == "{{.Created}}" ]] || exit 1
    field "$4" 2 || exit 1
    exit 0 ;;
  top)
    procs="$(field "$2" 3)" || exit 1
    [[ "$procs" == STOPPED ]] && { echo "Error response from daemon: container $2 is not running" >&2; exit 1; }
    echo "PID COMMAND"
    n=100; for p in $procs; do n=$((n+1)); echo "$n $p"; done
    exit 0 ;;
esac
exit 0
"""


_REAPER_VARS = (
    "PERSISTENT_BUILDX_BUILDERS",
    "IDLE_BUILDER_PATTERNS",
    "IDLE_BUILDER_MAX_AGE_HOURS",
    "REAP_IDLE_BUILDERS",
)


def _iso(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")


def _run(
    tmp_path: Path,
    builders: list[tuple[str, float, str]],
    *,
    call: str = "validate_persistent_builders && validate_idle_builder_patterns && reap_idle_builders",
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    assert BASH is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(DOCKER_SHIM, encoding="utf-8", newline="\n")
    docker.chmod(0o755)
    calls = tmp_path / "docker-calls.log"
    calls.touch()
    db = tmp_path / "builders.db"
    db.write_text(
        "".join(f"buildx_buildkit_{name}0|{_iso(age)}|{procs}\n" for name, age, procs in builders),
        encoding="utf-8",
        newline="\n",
    )

    script = CLEANUP.read_text(encoding="utf-8").split("# Emit a textfile-collector metric", 1)[0]
    harness = tmp_path / "reap.sh"
    harness.write_text(docker_shim_prelude(bin_dir) + script + f"\n{call}\n", encoding="utf-8", newline="\n")

    env = os.environ.copy()
    for var in _REAPER_VARS:
        env.pop(var, None)
    env.update(
        {
            "DOCKER_HOST": "unix:///nonexistent/docker-test-guard.sock",
            "DOCKER_CALLS": as_bash_path(calls),
            "BUILDERS_DB": as_bash_path(db),
            "LOG_DIR": as_bash_path(tmp_path / "logs"),
            "USER": "runner-test",
        }
    )
    env.update(extra_env or {})
    result = subprocess.run([BASH, as_bash_path(harness)], capture_output=True, text=True, check=False, env=env)
    return result, calls.read_text(encoding="utf-8").splitlines()


def _destructive(calls: list[str]) -> list[str]:
    verbs = ("rm ", "volume rm", "buildx rm", "container prune", "volume prune", "buildx prune", "builder prune")
    return [c for c in calls if c.startswith(verbs)]


def _reaped(calls: list[str], builder: str) -> bool:
    return (
        f"rm -f buildx_buildkit_{builder}0" in calls
        and f"volume rm buildx_buildkit_{builder}0_state" in calls
        and f"buildx rm {builder}" in calls
    )


def test_old_idle_ephemeral_builders_are_reaped(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        [("builder-9596afbf", 5, "buildkitd"), ("gm-ci-1234", 30, "docker-init buildkitd")],
    )

    assert result.returncode == 0, result.stderr
    assert _reaped(calls, "builder-9596afbf")
    assert _reaped(calls, "gm-ci-1234")


def test_stopped_old_ephemeral_builder_counts_as_idle(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, [("builder-dead", 10, "STOPPED")])

    assert result.returncode == 0, result.stderr
    assert _reaped(calls, "builder-dead")


def test_old_builder_with_an_active_build_is_kept(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, [("builder-busy", 8, "buildkitd buildkit-runc sh")])

    assert result.returncode == 0, result.stderr
    assert _destructive(calls) == []


def test_young_idle_builder_is_kept(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, [("builder-fresh", 1, "buildkitd")])

    assert result.returncode == 0, result.stderr
    assert _destructive(calls) == []


def test_persistent_ud_buildkit_is_never_touched(tmp_path: Path) -> None:
    """Even when old, idle and matching a pattern, the shared builder is spared."""
    result, calls = _run(
        tmp_path,
        [("ud-buildkit", 500, "buildkitd"), ("builder-old", 6, "buildkitd")],
        extra_env={"IDLE_BUILDER_PATTERNS": "builder-* gm-ci-* ud-*"},
    )

    assert result.returncode == 0, result.stderr
    assert not [c for c in calls if "ud-buildkit" in c and not c.startswith("ps")], calls
    assert _reaped(calls, "builder-old")


def test_non_matching_builder_is_kept(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, [("someones-builder", 48, "buildkitd")])

    assert result.returncode == 0, result.stderr
    assert _destructive(calls) == []


def test_dry_run_makes_no_destructive_calls(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, [("builder-old", 6, "buildkitd")], extra_env={"DRY_RUN": "1"})

    assert result.returncode == 0, result.stderr
    assert _destructive(calls) == []
    assert "builder-old" in (result.stdout + result.stderr)


def test_disabled_reaper_makes_no_calls(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, [("builder-old", 6, "buildkitd")], extra_env={"REAP_IDLE_BUILDERS": "0"})

    assert result.returncode == 0, result.stderr
    assert calls == []


def test_age_threshold_is_configurable(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, [("builder-old", 6, "buildkitd")], extra_env={"IDLE_BUILDER_MAX_AGE_HOURS": "12"})

    assert result.returncode == 0, result.stderr
    assert _destructive(calls) == []


@pytest.mark.parametrize(
    ("var", "bad"),
    [
        ("IDLE_BUILDER_PATTERNS", "builder-* *"),
        ("IDLE_BUILDER_PATTERNS", "Builder-*"),
        ("IDLE_BUILDER_PATTERNS", "../x*"),
        ("IDLE_BUILDER_MAX_AGE_HOURS", "0"),
        ("IDLE_BUILDER_MAX_AGE_HOURS", "3h"),
    ],
)
def test_invalid_config_fails_before_any_destructive_call(tmp_path: Path, var: str, bad: str) -> None:
    result, calls = _run(tmp_path, [("builder-old", 6, "buildkitd")], extra_env={var: bad})

    assert result.returncode == 2
    assert f"invalid {var}" in result.stderr
    assert _destructive(calls) == []


def test_cleanup_docker_runs_the_reaper(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        [("builder-old", 6, "buildkitd"), ("ud-buildkit", 500, "buildkitd")],
        call="cleanup_docker",
        extra_env={"PRUNE_ORPHAN_BUILDX_STATE": "0"},
    )

    assert result.returncode == 0, result.stderr
    assert _reaped(calls, "builder-old")
    assert not [c for c in _destructive(calls) if "ud-buildkit" in c]


def test_reaper_defaults_are_documented_in_script() -> None:
    text = CLEANUP.read_text(encoding="utf-8")
    assert 'IDLE_BUILDER_PATTERNS="${IDLE_BUILDER_PATTERNS-builder-* gm-ci-*}"' in text
    assert 'IDLE_BUILDER_MAX_AGE_HOURS="${IDLE_BUILDER_MAX_AGE_HOURS:-3}"' in text
