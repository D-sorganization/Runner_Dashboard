"""Tracked source must never contain committed merge-conflict markers (#2961).

The required ``quality-gate`` does not include the frontend jobs, so a marker in a
``.ts`` file merged green and left main's ``tsc`` red. This Python test runs in the
required suite and scans every tracked text file under the source roots.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = ("backend", "frontend/src", "deploy", "scripts", "tests")
# Built from parts so this file never matches its own pattern.
MARKER = re.compile("^(" + "<" * 7 + "|" + ">" * 7 + ")( |$)")


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *SOURCE_ROOTS],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return [REPO_ROOT / name for name in out.decode().split("\0") if name]


def find_conflict_markers(root: Path, paths: list[Path]) -> list[str]:
    """Return ``relpath:line`` for every line that opens or closes a conflict."""
    hits: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if MARKER.match(line):
                hits.append(f"{path.relative_to(root).as_posix()}:{lineno}")
    return hits


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
    hits = find_conflict_markers(REPO_ROOT, _tracked_files())
    assert not hits, f"committed merge-conflict markers found: {hits}"
