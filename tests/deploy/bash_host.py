"""Locate a bash the deploy tests can actually drive, and translate paths for it.

Windows hosts routinely carry two bashes: MSYS/Git Bash, which shares the
Windows filesystem, and ``C:\\Windows\\System32\\bash.exe``, the WSL launcher —
an equally valid POSIX bash that resolves paths inside a *separate* Linux
namespace. Which one ``shutil.which("bash")`` returns depends on the shell that
launched pytest: Git Bash leads with MSYS, PowerShell leads with WSL. Tests
that source deploy scripts therefore passed or failed purely on the launching
shell (RM#1164), which is not a property a test suite may have.

Resolution here does not trust PATH order or executable names. Every candidate
is *probed*: it must both stat and ``find`` a file this process just wrote.
That is the exact capability these tests need, so it is the thing worth
asserting — see ``_PROBE_SCRIPT`` for why the ``find`` half earns its keep.

Modules import ``BASH`` (``None`` when no suitable bash exists — guard with
``pytest.mark.skipif``) and ``as_bash_path`` for path translation.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_PROBE_TIMEOUT_S = 30


def _candidates() -> list[str]:
    """Bash executables to probe, most trustworthy first."""
    seen: set[str] = set()
    ordered: list[str] = []

    def add(value: str | None) -> None:
        if not value:
            return
        path = Path(value)
        if not path.exists():
            return
        key = str(path).lower() if os.name == "nt" else str(path)
        if key in seen:
            return
        seen.add(key)
        ordered.append(str(path))

    add(os.environ.get("BASH"))
    if os.name == "nt":
        # Ahead of PATH: under PowerShell, PATH leads with the WSL launcher.
        git = shutil.which("git")
        if git:
            git_root = Path(git).resolve().parent.parent
            # bin/bash.exe (the Git Bash launcher) prepends the MSYS coreutils
            # to PATH; the raw usr/bin/bash.exe inherits Windows' PATH verbatim,
            # so `find` there resolves to System32\find.exe. Launcher first.
            add(str(git_root / "bin" / "bash.exe"))
            add(str(git_root / "usr" / "bin" / "bash.exe"))
        for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            root = os.environ.get(var)
            if root:
                add(str(Path(root) / "Git" / "bin" / "bash.exe"))
                add(str(Path(root) / "Git" / "usr" / "bin" / "bash.exe"))
    add(shutil.which("bash"))
    add("/usr/bin/bash")
    add("/bin/bash")
    return ordered


@functools.cache
def _translate(path: str, bash: str) -> str:
    """Render *path* the way *bash* resolves it (MSYS ``/c/...`` on Windows)."""
    if os.name == "nt":
        try:
            out = subprocess.run(
                [bash, "-c", 'cygpath -u "$1"', "_", path],
                capture_output=True,
                text=True,
                check=False,
                timeout=_PROBE_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError):
            return Path(path).as_posix()
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    return Path(path).as_posix()


# Must see the probe file *and* reach a POSIX find, not System32\find.exe:
# the deploy scripts under test are `find -maxdepth`-shaped, and a raw MSYS
# bash that inherits Windows' PATH fails the second half while passing the
# first. Both halves are what the harness actually needs.
_PROBE_SCRIPT = 'test -f "$1" && find "$1" -maxdepth 0 >/dev/null 2>&1'


def _shares_host_filesystem(bash: str) -> bool:
    """True when *bash* can drive a file this process just wrote.

    WSL bash fails the stat: it runs, it is POSIX, and it sees a different disk.
    """
    with tempfile.TemporaryDirectory() as scratch:
        probe = Path(scratch) / "probe"
        probe.write_text("ok", encoding="utf-8")
        try:
            result = subprocess.run(
                [bash, "-c", _PROBE_SCRIPT, "_", _translate(str(probe), bash)],
                capture_output=True,
                text=True,
                check=False,
                timeout=_PROBE_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0


@functools.cache
def find_bash() -> str | None:
    """First bash on this host that shares this process's filesystem view.

    Cached: probing spawns subprocesses and several modules call this at import.
    """
    for candidate in _candidates():
        if _shares_host_filesystem(candidate):
            return candidate
    return None


BASH: str | None = find_bash()

SKIP_REASON = (
    "requires a POSIX bash that shares this host's filesystem (Git Bash on Windows; the WSL launcher does not qualify)"
)


def as_bash_path(path: Path) -> str:
    """Path form the resolved bash understands."""
    if BASH is None:
        return path.as_posix()
    return _translate(str(path), BASH)
