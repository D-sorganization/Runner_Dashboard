"""staff.runner stays under the 450-line merge_group cap (#2885)."""

from __future__ import annotations

from pathlib import Path

import staff.runner as runner_mod

_LINE_CAP = 450


def test_runner_module_stays_under_line_cap() -> None:
    path = Path(runner_mod.__file__).resolve().parent / "runner.py"
    lines = len(path.read_text(encoding="utf-8").splitlines())
    assert lines <= _LINE_CAP, f"runner.py has {lines} lines (cap {_LINE_CAP})"


def test_runner_public_import_surface_unchanged() -> None:
    assert runner_mod.StaffRunner is not None
    assert runner_mod.get_runner is not None
