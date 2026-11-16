#!/usr/bin/env python3
"""Fail-closed wrapper around local gitleaks pre-commit / pre-push hooks (#4451).

gitleaks can exit 0 when its internal ``git`` invocation fails, which makes a
broken toolchain look like a clean scan. This wrapper treats those cases as hook
failures.

At pre-push time (``PRE_COMMIT_FROM_REF`` / ``PRE_COMMIT_TO_REF`` are set) it
scans the pushed commit range with ``gitleaks detect`` instead of the empty
staged index.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG = Path(".gitleaks.toml")
_GIT_FAILURE_MARKERS = (
    "stderr is not empty",
    "[git] error:",
    "command aborted",
    "unknown option",
)


def _repo_root() -> Path:
    return Path.cwd()


def _staged_has_changes() -> bool:
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=_repo_root(),
        check=False,
    )
    return result.returncode == 1


def _pre_push_range() -> tuple[str, str] | None:
    from_ref = os.environ.get("PRE_COMMIT_FROM_REF", "").strip()
    to_ref = os.environ.get("PRE_COMMIT_TO_REF", "").strip()
    if from_ref and to_ref:
        return from_ref, to_ref
    return None


def _build_command() -> tuple[list[str], bool]:
    """Return argv and whether staged content is expected to be scanned."""
    config = str(_repo_root() / CONFIG)
    push_range = _pre_push_range()
    if push_range is not None:
        from_ref, to_ref = push_range
        cmd = [
            "gitleaks",
            "detect",
            "--source",
            ".",
            "--config",
            config,
            "--no-banner",
            "--redact",
            "--exit-code",
            "1",
            "--log-opts",
            f"{from_ref}..{to_ref}",
        ]
        return cmd, False
    cmd = [
        "gitleaks",
        "protect",
        "--staged",
        "--config",
        config,
        "--no-banner",
        "--redact",
    ]
    return cmd, _staged_has_changes()


def _run_gitleaks(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=_repo_root(),
        text=True,
        capture_output=True,
        check=False,
    )


def _suspicious_pass(result: subprocess.CompletedProcess[str], *, staged_expected: bool) -> str | None:
    if result.returncode != 0:
        return None
    combined = f"{result.stdout}\n{result.stderr}"
    for marker in _GIT_FAILURE_MARKERS:
        if marker in combined:
            return f"gitleaks git toolchain error ({marker})"
    if staged_expected and "0 commits scanned" in combined.lower():
        return "gitleaks reported 0 commits scanned with staged changes present"
    return None


def main(argv: list[str]) -> int:
    del argv
    push_range = _pre_push_range()
    if push_range is not None and push_range[0] == push_range[1]:
        logger.info("pre-push: empty range; skipping gitleaks")
        return 0
    cmd, staged_expected = _build_command()
    result = _run_gitleaks(cmd)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    reason = _suspicious_pass(result, staged_expected=staged_expected)
    if reason is not None:
        sys.stderr.write(f"gitleaks hook fail-closed: {reason}\n")
        return 1
    return result.returncode


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main(sys.argv[1:]))
