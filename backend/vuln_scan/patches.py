"""Candidate-patch guard (#2573, panel verdict q4 item 8).

A scanner's candidate patch is model output written after reading untrusted
code. A patch that changes CI workflows or the scanner contract could weaken the
very checks that review it, so such patches are flagged everywhere they appear
and are never handed to automation (no fix dispatch, no advisory text).
"""

from __future__ import annotations

import re

from vuln_scan.findings import normalize_path

PROTECTED_PREFIXES = (".github/", ".oss-scanner/")
_HEADER = re.compile(r"^(?:---|\+\+\+) (?:[ab]/)?(\S+)")
_GIT = re.compile(r"^diff --git a/(\S+) b/(\S+)")


def patch_paths(patch: str) -> list[str]:
    """Every file path a unified diff names, in order, without duplicates."""
    found: list[str] = []
    for line in patch.splitlines():
        git = _GIT.match(line)
        names = list(git.groups()) if git else []
        header = _HEADER.match(line)
        if header and header.group(1) != "/dev/null":
            names.append(header.group(1))
        for name in names:
            path = normalize_path(name)
            if path not in found:
                found.append(path)
    return found


def protected_paths(patch: str) -> list[str]:
    """Paths in ``patch`` under ``.github/`` or ``.oss-scanner/`` (case-insensitive)."""
    return [p for p in patch_paths(patch) if p.lower().startswith(PROTECTED_PREFIXES)]
