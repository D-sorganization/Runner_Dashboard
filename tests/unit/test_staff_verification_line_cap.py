"""staff.verification stays under the 450-line merge_group cap (#2885)."""

from __future__ import annotations

from pathlib import Path

import staff.verification as verification_mod

_LINE_CAP = 450


def test_verification_module_stays_under_line_cap() -> None:
    path = Path(verification_mod.__file__).resolve()
    lines = len(path.read_text(encoding="utf-8").splitlines())
    assert lines <= _LINE_CAP, f"verification.py has {lines} lines (cap {_LINE_CAP})"


def test_verification_public_import_surface_unchanged() -> None:
    assert verification_mod.GhCliPrProbe is not None
    assert verification_mod.verify_and_record is not None
