"""Staff verification and runner modules stay under the 450-line merge_group cap (#2885)."""

from __future__ import annotations

from pathlib import Path

import staff.runner as runner_mod
import staff.verification as verification_mod

_STAFF = Path(verification_mod.__file__).resolve().parent
_LINE_CAP = 450


def test_verification_and_runner_stay_under_line_cap() -> None:
    for path in (_STAFF / "verification.py", _STAFF / "runner.py"):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= _LINE_CAP, f"{path.name} has {lines} lines (cap {_LINE_CAP})"


def test_verification_public_import_surface_unchanged() -> None:
    assert verification_mod.GhCliPrProbe is not None
    assert verification_mod.verify_and_record is not None
    assert runner_mod.StaffRunner is not None
    assert runner_mod.get_runner is not None
