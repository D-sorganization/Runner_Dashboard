#!/usr/bin/env python3
"""Scan tracked source files for committed merge-conflict markers (#2961, #3408).

Shared by tests/test_no_merge_conflict_markers.py (the required pytest gate),
scripts/pre_pr.py and the ``check-conflict-markers`` pre-commit hook, so the
marker regex and scan roots live in exactly one place instead of being
re-implemented per consumer.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOTS = ("backend", "frontend/src", "deploy", "scripts", "tests", ".github")
# Built from parts so this file never matches its own pattern.
MARKER = re.compile("^(" + "<" * 7 + "|" + ">" * 7 + ")( |$)")


def tracked_files(root: Path, roots: Sequence[str] = SOURCE_ROOTS) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *roots],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    return [root / name for name in out.decode().split("\0") if name]


def find_conflict_markers(root: Path, paths: Sequence[Path]) -> list[str]:
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    root = args.repository_root.resolve()

    hits = find_conflict_markers(root, tracked_files(root))
    if hits:
        sys.stderr.write("::error::committed merge-conflict markers found:\n")
        for hit in hits:
            sys.stderr.write(f"  {hit}\n")
        return 1
    print("conflict markers: none found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
