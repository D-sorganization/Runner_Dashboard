"""Fail-closed wrapper around gitleaks for the pre-commit hook (#4451).

Two things made the plain ``gitleaks protect --staged`` hook pass without
scanning:

* gitleaks exits 0 when its own ``git`` call fails. It logs
  ``error="stderr is not empty"`` and then ``no leaks found``.
* At the pre-push stage nothing is staged, so there was nothing to scan.

This wrapper scans the staged changes at commit time and the pushed commit
range at push time (pre-commit exports ``PRE_COMMIT_FROM_REF`` and
``PRE_COMMIT_TO_REF`` there). It fails when gitleaks finds a leak, when
gitleaks is missing, and when gitleaks reports that it could not run git.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence

log = logging.getLogger("gitleaks_hook")

SCAN_DID_NOT_RUN = 2
DEFAULT_CONFIG = ".gitleaks.toml"
_REF_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# gitleaks log lines that mean the scan itself did not happen.
_SCAN_FAILED_RE = re.compile(r'ERR \[git\]|error="stderr is not empty"|\bFTL\b')

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _checked_ref(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not _REF_RE.fullmatch(value):
        raise ValueError(f"{name} is not a commit id: {value!r}")
    return value


def build_command(env: Mapping[str, str], *, config: str) -> list[str]:
    """Return the gitleaks command for this hook run.

    A push range from pre-commit selects a commit-range scan; otherwise the
    staged changes are scanned. Raises ``ValueError`` when a range ref is not
    a plain commit id.
    """
    common = ["--config", config, "--no-banner", "--redact"]
    if env.get("PRE_COMMIT_FROM_REF") or env.get("PRE_COMMIT_TO_REF"):
        start = _checked_ref(env, "PRE_COMMIT_FROM_REF")
        end = _checked_ref(env, "PRE_COMMIT_TO_REF")
        return ["gitleaks", "detect", "--source", ".", f"--log-opts={start}..{end}", *common]
    return ["gitleaks", "protect", "--staged", *common]


def verdict(result: subprocess.CompletedProcess[str]) -> tuple[int, str]:
    """Return ``(exit_code, reason)`` for a finished gitleaks run."""
    if result.returncode != 0:
        return result.returncode, f"gitleaks exited {result.returncode}"
    output = _ANSI_RE.sub("", f"{result.stdout or ''}\n{result.stderr or ''}")
    if _SCAN_FAILED_RE.search(output):
        return SCAN_DID_NOT_RUN, "gitleaks exited 0 but reported that git failed, so nothing was scanned"
    return 0, ""


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, refs validated in build_command
        command, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    runner: Runner = _run,
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    try:
        command = build_command(os.environ if env is None else env, config=args.config)
        result = runner(command)
    except FileNotFoundError:
        log.error("gitleaks is not installed or not on PATH; the secret scan did not run.")
        return SCAN_DID_NOT_RUN
    except ValueError as exc:
        log.error("gitleaks hook: %s; the secret scan did not run.", exc)
        return SCAN_DID_NOT_RUN
    for stream in (result.stdout, result.stderr):
        if stream and stream.strip():
            log.info("%s", stream.rstrip())
    code, reason = verdict(result)
    if code:
        log.error("gitleaks hook failed: %s.", reason)
    return code


if __name__ == "__main__":
    sys.exit(main())
