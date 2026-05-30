"""Contract tests for the per-runner tool-cache isolation in
``deploy/migrate-runner-units.sh``.

Root cause this guards against: on hosts where several runners shared a single
``.shared-tool-cache``, one job's ``actions/setup-python`` extraction raced
another job's cache access, corrupting the Python tree. The symptoms were
``rm: ... Directory not empty``, exit-127 ``python: command not found``, and
``ModuleNotFoundError: No module named 'http'``.

The fix gives every runner its OWN ``RUNNER_TOOL_CACHE`` via the per-unit
systemd drop-in that ``migrate-runner-units.sh`` already writes. These tests
are deliberately source-level (no systemd required): they assert the script is
syntactically valid and that the drop-in writer emits a per-runner
``RUNNER_TOOL_CACHE`` derived from each unit's working directory.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "deploy" / "migrate-runner-units.sh"


def _find_bash() -> str | None:
    candidates = [
        os.environ.get("BASH"),
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files\Git\bin\bash.exe",
        "/usr/bin/bash",
        "/bin/bash",
        shutil.which("bash"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            if candidate.lower().endswith(r"system32\bash.exe"):
                continue
            return candidate
    return None


BASH = _find_bash()


def _as_bash_path(p: Path) -> str:
    return str(p).replace("\\", "/")


def test_script_exists() -> None:
    assert SCRIPT.is_file(), f"missing script: {SCRIPT}"


@pytest.mark.skipif(BASH is None, reason="POSIX bash required for `bash -n`")
def test_script_is_syntactically_valid() -> None:
    result = subprocess.run(
        [BASH or "bash", "-n", _as_bash_path(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"bash -n failed: {result.stderr}"


def test_dropin_sets_per_runner_tool_cache() -> None:
    """The drop-in writer must emit a RUNNER_TOOL_CACHE Environment= line."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "Environment=RUNNER_TOOL_CACHE=" in src, (
        "drop-in must export a per-runner RUNNER_TOOL_CACHE so runners stop sharing a tool cache"
    )


def test_tool_cache_defaults_to_runner_local_work_tool() -> None:
    """When RUNNER_TOOL_CACHE_ROOT is unset, the cache must default to the
    runner-local ``<WorkingDirectory>/_work/_tool`` — the path that
    deploy/runner-cleanup.sh already garbage-collects (avoids unbounded
    growth in a separate, unmanaged location)."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "/_work/_tool" in src
    assert "RUNNER_TOOL_CACHE_ROOT" in src


def test_tool_cache_root_override_is_per_runner() -> None:
    """An explicit RUNNER_TOOL_CACHE_ROOT must still be namespaced per runner
    (root/<runner_name>) so two runners on one host never collide."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "${RUNNER_TOOL_CACHE_ROOT%/}/${runner_name}" in src
