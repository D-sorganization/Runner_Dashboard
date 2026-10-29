"""Tracked source must never contain committed merge-conflict markers (#2961).

The required ``quality-gate`` does not include the frontend jobs, so a marker in a
``.ts`` file merged green and left main's ``tsc`` red. This Python test runs in the
required suite and scans every tracked text file under the source roots.

The regex, scan roots and scanning logic live in scripts.check_conflict_markers
(#3408) so this test, the pre-commit hook and scripts/pre_pr.py share one
implementation instead of three copies.
"""

from __future__ import annotations

from pathlib import Path

from scripts.check_conflict_markers import find_conflict_markers, tracked_files

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_detector_flags_both_marker_kinds(tmp_path: Path) -> None:
    sample = tmp_path / "x.ts"
    sample.write_text(
        "a\n" + "<" * 7 + " HEAD\n=======\n" + ">" * 7 + " origin/main\n",
        encoding="utf-8",
    )
    assert find_conflict_markers(tmp_path, [sample]) == ["x.ts:2", "x.ts:4"]


def test_detector_ignores_lookalikes(tmp_path: Path) -> None:
    sample = tmp_path / "y.md"
    sample.write_text(
        "=======\n" + "<" * 10 + " not a marker\nx " + "<" * 7 + " y\n",
        encoding="utf-8",
    )
    assert find_conflict_markers(tmp_path, [sample]) == []


def test_tracked_source_has_no_conflict_markers() -> None:
    hits = find_conflict_markers(REPO_ROOT, tracked_files(REPO_ROOT))
    assert not hits, f"committed merge-conflict markers found: {hits}"
