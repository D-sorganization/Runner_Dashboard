"""Tests for deploy/reap-wsl-leaked-chrome.sh (issue #1678).

Reaps Windows chrome.exe processes leaked into WSL runner hosts by
lhci/chrome-launcher via /mnt/c interop. The script drives a fake
`powershell.exe` on PATH (or POWERSHELL_BIN) so no real PowerShell/Chrome
process is ever touched by this suite.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "deploy" / "reap-wsl-leaked-chrome.sh"
MAINTENANCE_SCRIPT_PATH = REPO_ROOT / "deploy" / "scheduled-dashboard-maintenance.sh"


def _find_bash() -> str | None:
    """Locate a real (non-WSL-launcher) bash, preferring Git for Windows.

    Plain "bash" on Windows PATH can resolve to the WSL launcher shim at
    System32\\bash.exe, which does not accept a native Windows/MSYS path the
    way Git Bash does. Prefer Git Bash explicitly when present.
    """
    git_bash = Path(r"C:\Program Files\Git\usr\bin\bash.exe")
    if git_bash.exists():
        return str(git_bash)
    return shutil.which("bash")


BASH_PATH = _find_bash()
BASH_UNAVAILABLE = BASH_PATH is None
SKIP_REASON = "bash is unavailable on this platform"


def _make_fake_powershell(tmp_path: Path, record_path: Path) -> Path:
    """Write a fake powershell.exe that records its argv and stdin."""
    fake = tmp_path / "fake-bin" / "powershell.exe"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'printf \'%s\\n\' "$@" > "{record_path}.args"\n'
        f'cat > "{record_path}.stdin" 2>/dev/null || true\n'
        'echo "3"\n',
        encoding="utf-8",
        newline="\n",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return fake


def _run_script(
    tmp_path: Path,
    *,
    env_overrides: dict[str, str] | None = None,
    powershell_path: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    assert BASH_PATH is not None
    record_path = tmp_path / "recorded"
    env = os.environ.copy()
    env["PATH"] = os.environ.get("PATH", "")
    if powershell_path is not None:
        env["POWERSHELL_BIN"] = str(powershell_path)
    if env_overrides:
        env.update(env_overrides)

    result = subprocess.run(
        [BASH_PATH, SCRIPT_PATH.as_posix()],
        env=env,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result, record_path


def test_script_exists() -> None:
    assert SCRIPT_PATH.is_file(), f"Missing {SCRIPT_PATH}"


@pytest.mark.skipif(BASH_UNAVAILABLE, reason=SKIP_REASON)
def test_script_is_executable_in_git_index() -> None:
    # Windows filesystems do not preserve the POSIX exec bit, so the
    # meaningful check is what git recorded for the tracked blob mode
    # (100755), not os.stat() on the working tree.
    result = subprocess.run(
        ["git", "ls-files", "-s", "deploy/reap-wsl-leaked-chrome.sh"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode == 128 and "not a git repository" in result.stderr:
        # A worktree admin dir created on Windows records a Windows-style
        # gitdir path; a git binary run from inside WSL against that same
        # tree over /mnt/c cannot resolve it. This is an environment/tooling
        # limitation of that cross-mount combination, not a property of the
        # tracked file mode being asserted here.
        pytest.skip("git worktree admin path not resolvable from this environment")
    assert result.stdout.startswith("100755"), (
        f"expected git mode 100755 for reap-wsl-leaked-chrome.sh, got: {result.stdout!r}"
    )


@pytest.mark.skipif(BASH_UNAVAILABLE, reason=SKIP_REASON)
def test_snippet_contains_lighthouse_pattern_and_age_filter(tmp_path: Path) -> None:
    fake_ps = _make_fake_powershell(tmp_path, tmp_path / "recorded")
    result, record_path = _run_script(tmp_path, powershell_path=fake_ps)

    assert result.returncode == 0, result.stderr

    args_file = record_path.with_suffix(".args")
    stdin_file = record_path.with_suffix(".stdin")
    assert args_file.exists(), "fake powershell.exe was never invoked"

    invocation = args_file.read_text(encoding="utf-8") + (
        stdin_file.read_text(encoding="utf-8") if stdin_file.exists() else ""
    )

    assert "chrome.exe" in invocation
    assert r"\AppData\Local\lighthouse." in invocation
    assert "CreationDate" in invocation
    # Default age threshold of 2 hours must appear somewhere in the snippet.
    assert "2" in invocation


@pytest.mark.skipif(BASH_UNAVAILABLE, reason=SKIP_REASON)
def test_dry_run_does_not_stop_processes(tmp_path: Path) -> None:
    fake_ps = _make_fake_powershell(tmp_path, tmp_path / "recorded")
    result, record_path = _run_script(tmp_path, env_overrides={"DRY_RUN": "1"}, powershell_path=fake_ps)

    assert result.returncode == 0, result.stderr
    args_file = record_path.with_suffix(".args")
    stdin_file = record_path.with_suffix(".stdin")
    invocation = args_file.read_text(encoding="utf-8") + (
        stdin_file.read_text(encoding="utf-8") if stdin_file.exists() else ""
    )
    assert "Stop-Process" not in invocation
    assert "dry" in result.stdout.lower() or "dry" in invocation.lower()


@pytest.mark.skipif(BASH_UNAVAILABLE, reason=SKIP_REASON)
def test_real_run_includes_stop_process(tmp_path: Path) -> None:
    fake_ps = _make_fake_powershell(tmp_path, tmp_path / "recorded")
    result, record_path = _run_script(tmp_path, env_overrides={"DRY_RUN": "0"}, powershell_path=fake_ps)

    assert result.returncode == 0, result.stderr
    args_file = record_path.with_suffix(".args")
    stdin_file = record_path.with_suffix(".stdin")
    invocation = args_file.read_text(encoding="utf-8") + (
        stdin_file.read_text(encoding="utf-8") if stdin_file.exists() else ""
    )
    assert "Stop-Process" in invocation


@pytest.mark.skipif(BASH_UNAVAILABLE, reason=SKIP_REASON)
def test_custom_age_threshold_honoured(tmp_path: Path) -> None:
    fake_ps = _make_fake_powershell(tmp_path, tmp_path / "recorded")
    result, record_path = _run_script(
        tmp_path,
        env_overrides={"LEAKED_CHROME_MAX_AGE_HOURS": "6"},
        powershell_path=fake_ps,
    )

    assert result.returncode == 0, result.stderr
    args_file = record_path.with_suffix(".args")
    stdin_file = record_path.with_suffix(".stdin")
    invocation = args_file.read_text(encoding="utf-8") + (
        stdin_file.read_text(encoding="utf-8") if stdin_file.exists() else ""
    )
    assert "6" in invocation


@pytest.mark.skipif(BASH_UNAVAILABLE, reason=SKIP_REASON)
def test_missing_powershell_exits_zero_with_warning(tmp_path: Path) -> None:
    env = os.environ.copy()
    # Point PATH at an empty dir and POWERSHELL_BIN at a nonexistent binary so
    # the script cannot find any powershell.exe anywhere.
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    env["PATH"] = str(empty_bin)
    env["POWERSHELL_BIN"] = str(tmp_path / "does-not-exist-powershell.exe")

    assert BASH_PATH is not None
    result = subprocess.run(
        [BASH_PATH, SCRIPT_PATH.as_posix()],
        env=env,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    combined = (result.stdout + result.stderr).lower()
    assert "warn" in combined
    assert "powershell" in combined


@pytest.mark.skipif(BASH_UNAVAILABLE, reason=SKIP_REASON)
def test_maintenance_script_references_reaper_under_wsl_guard() -> None:
    content = MAINTENANCE_SCRIPT_PATH.read_text(encoding="utf-8")
    assert "reap-wsl-leaked-chrome.sh" in content

    # Static check: the reaper invocation must be reachable only through a
    # WSL-detection guard (binfmt_misc interop marker or WSL_DISTRO_NAME).
    guard_markers = ("WSLInterop", "WSL_DISTRO_NAME")
    assert any(marker in content for marker in guard_markers), (
        "maintenance script must gate the reaper call behind a WSL detection guard"
    )

    reaper_index = content.index("reap-wsl-leaked-chrome.sh")
    preceding = content[:reaper_index]
    last_guard_pos = max(
        (preceding.rfind(marker) for marker in guard_markers if marker in preceding),
        default=-1,
    )
    assert last_guard_pos != -1, "no WSL guard marker precedes the reaper invocation in the maintenance script"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
