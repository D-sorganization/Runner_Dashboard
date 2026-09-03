"""``cleanup_litter_in`` must survive a vanishing entry but not a real fault.

`runner-cleanup.service` sat in ``Result: exit-code`` for hours because CI
jobs delete their own pip temporaries between ``find``'s readdir and its
``-exec``. ``find`` then prints ``No such file or directory`` and exits
non-zero, which under the script's ``set -Eeuo pipefail`` failed the whole
unit and skipped every later stage of the daily pass -- while the hourly
disk-guard pass kept working, so the outage was invisible.

The fix must be surgical: a vanished entry is benign, but "Permission
denied" is exactly the ownership symptom this series is chasing and must
still fail loudly. These tests source the real shell function and exercise
both paths.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
_SCRIPT = _REPO / "deploy" / "runner-cleanup.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or os.name != "posix",
    reason="requires bash on a POSIX host",
)


def _harness(body: str, fake_find: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run ``body`` with the real ``cleanup_litter_in`` in scope.

    The function is extracted from the script rather than sourcing the whole
    file, which would execute its argument parsing and systemd probing.
    """
    src = _SCRIPT.read_text(encoding="utf-8")
    start = src.index("cleanup_litter_in() {")
    end = src.index("\n}\n", start) + len("\n}\n")
    function = src[start:end]

    preamble = textwrap.dedent(
        """
        set -Eeuo pipefail
        DRY_RUN=0
        log() { printf '%s\\n' "$*"; }
        """
    )
    script = "\n".join([preamble, fake_find or "", function, body])
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False
    )


def test_vanished_entry_does_not_fail_the_pass(tmp_path: Path) -> None:
    """The exact production race, simulated by a stub ``find``."""
    fake_find = textwrap.dedent(
        """
        find() {
            echo "find: '/tmp/pip-unpack-54p8nq45': No such file or directory" >&2
            echo "find: '/tmp/pip-metadata-oywhk0op': No such file or directory" >&2
            return 1
        }
        """
    )
    result = _harness(f'cleanup_litter_in "{tmp_path}" 360; echo "rc=$?"', fake_find)
    assert "rc=0" in result.stdout, result.stderr
    assert "vanished mid-scan" in result.stdout


def test_permission_denied_still_fails(tmp_path: Path) -> None:
    """A real fault must not be swallowed with the benign race."""
    fake_find = textwrap.dedent(
        """
        find() {
            echo "find: '/tmp/pip-install-abc': Permission denied" >&2
            return 1
        }
        """
    )
    result = _harness(
        f'cleanup_litter_in "{tmp_path}" 360 || echo "rc=$?"', fake_find
    )
    assert "rc=1" in result.stdout, result.stdout + result.stderr
    assert "Permission denied" in result.stderr


def test_mixed_output_fails_on_the_real_fault(tmp_path: Path) -> None:
    fake_find = textwrap.dedent(
        """
        find() {
            echo "find: '/tmp/pip-unpack-1': No such file or directory" >&2
            echo "find: '/tmp/pip-install-2': Permission denied" >&2
            return 1
        }
        """
    )
    result = _harness(
        f'cleanup_litter_in "{tmp_path}" 360 || echo "rc=$?"', fake_find
    )
    assert "rc=1" in result.stdout


def test_clean_sweep_reports_success_and_removes_aged_litter(tmp_path: Path) -> None:
    """End-to-end against the real ``find``: aged litter goes, live files stay."""
    aged = tmp_path / "pip-install-old"
    aged.mkdir()
    (aged / "x").write_text("x", encoding="utf-8")
    fresh = tmp_path / "pip-install-new"
    fresh.mkdir()
    keep = tmp_path / "important-job-dir"
    keep.mkdir()

    old = 1_600_000_000
    os.utime(aged, (old, old))

    result = _harness(f'cleanup_litter_in "{tmp_path}" 360; echo "rc=$?"')
    assert "rc=0" in result.stdout, result.stderr
    assert not aged.exists(), "aged litter must be reaped"
    assert fresh.exists(), "fresh litter is inside the age window"
    assert keep.exists(), "non-litter names must never be touched"


def test_dry_run_makes_no_changes(tmp_path: Path) -> None:
    aged = tmp_path / "pip-install-old"
    aged.mkdir()
    old = 1_600_000_000
    os.utime(aged, (old, old))

    src = _SCRIPT.read_text(encoding="utf-8")
    start = src.index("cleanup_litter_in() {")
    end = src.index("\n}\n", start) + len("\n}\n")
    script = "\n".join(
        [
            "set -Eeuo pipefail",
            "DRY_RUN=1",
            "log() { printf '%s\\n' \"$*\"; }",
            src[start:end],
            f'cleanup_litter_in "{tmp_path}" 360; echo "rc=$?"',
        ]
    )
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False
    )
    assert "rc=0" in result.stdout
    assert aged.exists(), "dry-run must not delete anything"
