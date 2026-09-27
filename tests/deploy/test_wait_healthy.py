"""Tests for the shared deploy health gate ``wait_healthy`` in deploy/lib.sh (#1656).

The old ``_wait_healthy`` in update-deployed.sh slept after its last failed check and
returned without checking again, so a service that came up during the final sleep
was rolled back. ``wait_healthy`` checks after every sleep, including the last.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "deploy" / "lib.sh"
UPDATE_SCRIPT = ROOT / "deploy" / "update-deployed.sh"
BASH = shutil.which("bash")

pytestmark = [pytest.mark.unit, pytest.mark.skipif(BASH is None, reason="bash not available")]


def _run(healthy_on_call: int, delays: str | None = None) -> subprocess.CompletedProcess[str]:
    """Source lib.sh, stub the check (healthy from call N) and sleep, then run wait_healthy."""
    env_line = f"export DEPLOY_HEALTH_DELAYS='{delays}'" if delays is not None else "unset DEPLOY_HEALTH_DELAYS"
    script = f"""
set -euo pipefail
source "$1"
{env_line}
calls=0
slept=""
check() {{ calls=$((calls+1)); [[ $calls -ge {healthy_on_call} ]]; }}
sleep() {{ slept="$slept $1"; }}
rc=0
wait_healthy check || rc=$?
echo "rc=$rc calls=$calls slept=[${{slept# }}]"
"""
    return subprocess.run(
        [BASH, "-c", script, "wait_healthy_test", LIB.as_posix()], capture_output=True, text=True, check=True
    )


def _result(proc: subprocess.CompletedProcess[str]) -> str:
    return proc.stdout.strip().splitlines()[-1]


def test_healthy_at_once_does_not_sleep() -> None:
    assert _result(_run(1, "1 2 4")) == "rc=0 calls=1 slept=[]"


def test_a_service_that_comes_up_during_the_last_sleep_passes() -> None:
    # Checks at 0, after 1, after 2, after 4: the 4th check is the one the old loop never made.
    assert _result(_run(4, "1 2 4")) == "rc=0 calls=4 slept=[1 2 4]"


def test_never_healthy_fails_after_one_check_per_delay_plus_the_first() -> None:
    assert _result(_run(99, "1 2 4")) == "rc=1 calls=4 slept=[1 2 4]"


def test_default_window_is_at_least_a_minute() -> None:
    out = _result(_run(99))
    slept = out.split("slept=[", 1)[1].rstrip("]").split()
    assert out.startswith("rc=1")
    assert sum(int(s) for s in slept) >= 60


def test_update_deployed_uses_the_shared_gate() -> None:
    content = UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert "wait_healthy _check_health" in content
    assert "_wait_healthy" not in content
