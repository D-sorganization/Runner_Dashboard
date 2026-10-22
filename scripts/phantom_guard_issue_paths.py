#!/usr/bin/env python3
"""Extract repository file paths from an issue body for anti-phantom Rule 3.

HTTP route literals such as ``/api/v1/metrics/weekly-summary`` must not be treated
as repo paths (they are not ``api/v1/...`` files on disk).
"""

from __future__ import annotations

import re
import sys

_REPO_PATH = re.compile(
    r"(?:backend|frontend/src|src|tests|rust_core)/[A-Za-z0-9_./-]+",
)


def _strip_trailing_punctuation(path: str) -> str:
    return path.rstrip(".,;)`")


def issue_body_repo_paths(body: str) -> list[str]:
    """Sorted unique repo-relative paths mentioned in ``body``."""
    cleaned = {_strip_trailing_punctuation(p) for p in _REPO_PATH.findall(body)}
    return sorted(cleaned)


def main() -> None:
    print("\n".join(issue_body_repo_paths(sys.stdin.read())))


if __name__ == "__main__":
    main()
