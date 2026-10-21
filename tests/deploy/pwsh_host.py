"""Shared PowerShell subprocess helpers for deploy script tests.

GitHub-hosted ``ubuntu-latest`` runners often exceed a 10s ``subprocess`` cap on
the first ``pwsh -File`` invocation (cold JIT / assembly load). Merge-group CI
runs the full pytest suite with xdist, so each worker pays that cost once.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from typing import Sequence

# Observed flake: ``test_drain_marker_exits_before_validation_or_recovery`` timed
# out at 10s on merge_group run 39568146375 while the script itself exits in <1s
# once pwsh is warm. 60s is a hard ceiling for a marker-only early-exit path.
PWSH_FILE_TIMEOUT_SECONDS = 60

_warmup_lock = threading.Lock()
_warmed_up = False


def find_pwsh() -> str | None:
    for candidate in ("pwsh", "powershell"):
        path = shutil.which(candidate)
        if path:
            return path
    return None


def ensure_pwsh_warmed_up(pwsh: str) -> None:
    """Pay the one-time pwsh startup cost before timing a script under test."""
    global _warmed_up
    with _warmup_lock:
        if _warmed_up:
            return
        subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-Command", "$PSVersionTable.PSVersion.ToString()"],
            capture_output=True,
            text=True,
            check=False,
            timeout=PWSH_FILE_TIMEOUT_SECONDS,
        )
        _warmed_up = True


def run_pwsh_file(
    pwsh: str,
    script_path: str,
    args: Sequence[str] = (),
    *,
    timeout: int = PWSH_FILE_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run ``pwsh -File`` after warm-up with a CI-safe timeout."""
    ensure_pwsh_warmed_up(pwsh)
    return subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", script_path, *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def run_pwsh_command(
    pwsh: str,
    script_body: str,
    *,
    timeout: int = PWSH_FILE_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run ``pwsh -Command`` after warm-up (dot-sourced helper tests)."""
    ensure_pwsh_warmed_up(pwsh)
    return subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", script_body],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
