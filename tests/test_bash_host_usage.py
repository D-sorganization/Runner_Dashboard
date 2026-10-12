"""Guard: tests must not launch a bare ``bash`` (Runner_Dashboard#1939).

On Windows a bare ``bash`` resolves via PATH, and under PowerShell/pre-push hooks
that is ``C:\\Windows\\System32\\bash.exe`` -- the WSL launcher, which sees a
different filesystem and emits UTF-16 stderr. ``tests/deploy/bash_host.py``
probes for a usable bash; every subprocess invocation in the suite goes through it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from bash_host import find_bash

_TESTS = Path(__file__).resolve().parent
_BARE_BASH_ARGV = re.compile(r"""\[\s*["']bash["']\s*[,\]]""")
_WSL_LAUNCHER = "system32"


def _test_sources() -> list[Path]:
    return sorted(p for p in _TESTS.rglob("test_*.py") if p != Path(__file__).resolve())


def test_no_test_launches_bare_bash_argv() -> None:
    offenders = [
        f"{path.relative_to(_TESTS)}:{lineno}"
        for path in _test_sources()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _BARE_BASH_ARGV.search(line)
    ]
    assert not offenders, f"route these through tests/deploy/bash_host.find_bash(): {offenders}"


@pytest.mark.skipif(os.name != "nt", reason="the WSL launcher only exists on Windows")
def test_resolved_bash_is_not_the_wsl_launcher() -> None:
    bash = find_bash()
    if bash is None:
        pytest.skip("no host-filesystem bash installed")
    assert _WSL_LAUNCHER not in bash.lower()


def test_conftest_exposes_bash_host_to_top_level_tests() -> None:
    import sys

    assert str(_TESTS / "deploy") in sys.path
