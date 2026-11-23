#!/usr/bin/env python3
"""Run fragment and formatting hooks against the feature branch diff."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable


def main(run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> int:
    target = os.environ.get("PRE_COMMIT_TO_REF", "HEAD")
    result = run(["git", "merge-base", "origin/main", target], capture_output=True, text=True, check=False)
    if result.returncode or not result.stdout.strip():
        print("pre-push: cannot resolve merge-base with origin/main", file=sys.stderr)
        return 1
    base = result.stdout.strip()
    status = 0
    for hook in ("changes-fragment", "prettier"):
        result = run(
            ["pre-commit", "run", hook, "--hook-stage", "pre-commit", "--from-ref", base, "--to-ref", target],
            check=False,
        )
        status |= result.returncode
    return status


if __name__ == "__main__":
    raise SystemExit(main())
