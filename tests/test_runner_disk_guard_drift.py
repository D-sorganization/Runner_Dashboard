"""Runner disk guard vendored copy must match Repository_Management (RM #2230)."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_runner_disk_guard_drift as drift  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_manifest_pins_vendored_file_hashes() -> None:
    errors = drift.verify_manifest(_REPO_ROOT)
    assert errors == []


def test_main_manifest_only_passes() -> None:
    rc = drift.main(["--repo-root", str(_REPO_ROOT), "--manifest-only"])
    assert rc == 0
