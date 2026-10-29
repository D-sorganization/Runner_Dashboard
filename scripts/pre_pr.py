#!/usr/bin/env python3
"""Run pre-PR local checks mirroring CI gates (RM-6, #3408).

AGENTS.md's synced PR Lifecycle rule says to run `python -m scripts.pre_pr`
before pushing. This repo had neither that module nor a shared
implementation, so the command failed outright. This is RD's local entry
point for that command: it composes the existing merge-gate mirror
(scripts.verify_merge_gates: API contract + secrets baseline) with the
conflict-marker scan (scripts.check_conflict_markers) that CI Standard's
required `quality-gate` runs via tests/test_no_merge_conflict_markers.py.

Repository_Management's own scripts/pre_pr.py additionally wires its
pr_cap_preflight and pr_closing_ref checks, which are RM-specific concepts
with no equivalent here, so this module is not a byte-for-byte copy.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from scripts import check_conflict_markers, verify_merge_gates


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--base", default="origin/main", help="Ref the PR will merge into")
    args = parser.parse_args(argv)

    root_argv = ["--repository-root", str(args.repository_root)]
    gates_status = verify_merge_gates.main([*root_argv, "--base", args.base])
    markers_status = check_conflict_markers.main(root_argv)

    if gates_status or markers_status:
        return 1
    print("pre_pr: all local gates passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
