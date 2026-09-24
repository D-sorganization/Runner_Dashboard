"""Static and argument checks for ``deploy/staff-node-acceptance.sh`` (issue #1273).

The script inspects a live node (dashboard, systemd, sign-ins), so its checks
run on the nodes themselves; CI verifies that it parses, documents its usage,
refuses to run without a scheduler expectation and stays read-only by default.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "staff-node-acceptance.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, timeout=60, check=False)


def test_script_parses() -> None:
    assert BASH is not None
    assert subprocess.run([BASH, "-n", str(SCRIPT)], check=False).returncode == 0


def test_scheduler_expectation_is_required() -> None:
    result = _run()
    assert result.returncode == 2
    assert "--scheduler on|off is required" in result.stderr


def test_unknown_argument_is_rejected() -> None:
    assert _run("--scheduler", "off", "--bogus").returncode == 2


def test_help_documents_both_modes() -> None:
    out = _run("--help").stdout
    assert "--scheduler off" in out and "--dispatch" in out


def test_only_dispatch_writes() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    writes = [line for line in text.splitlines() if "-X POST" in line or "-X PUT" in line]
    assert len(writes) == 1 and "ad-hoc/run" in writes[0]
    assert text.index('if [ "$DISPATCH" = 1 ]') < text.index("-X POST")
