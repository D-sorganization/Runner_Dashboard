"""Runner disk guard vendored copy must match Repository_Management (RM #2230)."""

from __future__ import annotations

import json
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


def test_manifest_records_rm_merge_pr() -> None:
    manifest_path = _REPO_ROOT / "tests" / "contracts" / "runner_disk_guard_upstream.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest.get("upstream_merge_pr") == 2230
    assert manifest.get("upstream_repository") == drift.UPSTREAM_REPO
    ref = manifest.get("upstream_ref")
    assert isinstance(ref, str) and len(ref) == 40
    assert ref != "main"
