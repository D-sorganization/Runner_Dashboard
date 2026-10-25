#!/usr/bin/env python3
"""Run the required merge-path gates locally before opening a PR (#2655).

Checks the generated API contract (Frontend Tests "Verify generated API contract
types") and the detect-secrets baseline (CI Secrets "Audit baseline integrity").
CI Secrets calls ``--only baseline`` so local and CI use one implementation.
Standard library only: CI runs it with the scanner virtualenv's interpreter.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

BASELINE = ".secrets.baseline"
EXCLUDE_FILES = (
    r"(package-lock\.json|uv\.lock|\.tsbuildinfo$|node_modules/|\.venv/|\.ci-ds-venv/|\.git/|\.secrets\.baseline)"
)
CONTRACT_COMMAND = ("bash", "scripts/gen-api-client.sh", "--check")
# Mirrors run_api_contract in frontend-tests.yml (parity-tested).
_FRONTEND_EXACT = frozenset(
    {
        "package.json",
        "package-lock.json",
        "vite.config.ts",
        "vitest.config.ts",
        "tsconfig.json",
        "tsconfig.app.json",
        "tsconfig.node.json",
    }
)
Runner = Callable[..., subprocess.CompletedProcess[str]]


def touches_api_contract(paths: Iterable[str]) -> bool:
    """True when CI would run the API contract check for these changed paths."""
    return any(path.startswith(("frontend/", "scripts/", "backend/")) or path in _FRONTEND_EXACT for path in paths)


def normalized_results(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Return the baseline ``results`` block keyed by POSIX path (Windows-made baselines too)."""
    normalized: dict[str, list[dict[str, Any]]] = {}
    for filename, findings in (document.get("results") or {}).items():
        key = str(PurePosixPath(filename.replace("\\", "/")))
        bucket = normalized.setdefault(key, [])
        for finding in findings:
            item = dict(finding)
            if "filename" in item:
                item["filename"] = str(PurePosixPath(item["filename"].replace("\\", "/")))
            bucket.append(item)
    return normalized


def _describe(results: dict[str, list[dict[str, Any]]]) -> set[str]:
    return {
        f"{path}:{item.get('line_number')} {item.get('type')} {item.get('hashed_secret')}"
        for path, findings in results.items()
        for item in findings
    }


def baseline_drift(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Describe findings added to or removed from the baseline by a rescan."""
    old, new = _describe(normalized_results(before)), _describe(normalized_results(after))
    return [f"new: {entry}" for entry in sorted(new - old)] + [f"stale: {entry}" for entry in sorted(old - new)]


def _scanner_command(scanner_python: str | None) -> list[str]:
    if scanner_python:
        return [scanner_python, "-m", "detect_secrets"]
    executable = shutil.which("detect-secrets")
    return [executable] if executable else [sys.executable, "-m", "detect_secrets"]


def audit_baseline(root: Path, *, scanner_python: str | None = None, run: Runner | None = None) -> list[str]:
    """Rescan against a temporary baseline copy; the committed baseline is never rewritten."""
    run = run or subprocess.run
    committed = root / BASELINE
    if not committed.is_file():
        return [f"missing {BASELINE}"]
    before = json.loads(committed.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as temp_dir:
        scratch = Path(temp_dir) / BASELINE
        shutil.copyfile(committed, scratch)
        command = [
            *_scanner_command(scanner_python),
            "scan",
            "--baseline",
            str(scratch),
            "--exclude-files",
            EXCLUDE_FILES,
        ]
        proc = run(command, cwd=root, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return [f"detect-secrets scan failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()}"]
        after = json.loads(scratch.read_text(encoding="utf-8"))
    return baseline_drift(before, after)


def changed_paths(root: Path, base: str, run: Runner = subprocess.run) -> list[str]:
    """Paths changed since the merge base with ``base``, including uncommitted and untracked files."""

    def git(*args: str) -> str:
        return run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout

    merge_base = git("merge-base", base, "HEAD").strip()
    names = git("diff", "--name-only", merge_base).splitlines()
    names += git("ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({name for name in names if name})


def check_contract(root: Path, paths: Sequence[str], run: Runner = subprocess.run) -> list[str]:
    if not touches_api_contract(paths):
        return []
    proc = run(list(CONTRACT_COMMAND), cwd=root, capture_output=True, text=True, check=False)
    if proc.returncode == 0:
        return []
    tail = " | ".join((proc.stdout or proc.stderr or "").strip().splitlines()[-3:])
    return [
        f"API contract check failed (exit {proc.returncode}): {tail}. If it reports drift, run "
        "`npm run generate-api` and commit frontend/src/lib/openapi.json and api-types.ts"
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--base", default="origin/main", help="Ref the PR will merge into")
    parser.add_argument("--only", choices=("contract", "baseline"), help="Run one gate (CI Secrets uses baseline)")
    parser.add_argument("--scanner-python", help="Interpreter that has detect-secrets installed")
    args = parser.parse_args(argv)
    root = args.repository_root.resolve()
    problems: list[str] = []
    if args.only != "baseline":
        problems += check_contract(root, changed_paths(root, args.base))
    if args.only != "contract":
        problems += [
            f"secrets baseline drift: {item}" for item in audit_baseline(root, scanner_python=args.scanner_python)
        ]
    for problem in problems:
        sys.stderr.write(f"::error::{problem}\n")
    if any(p.startswith("secrets baseline drift") for p in problems):
        sys.stderr.write("Remove the value, or rescan and audit .secrets.baseline (detect-secrets audit).\n")
    sys.stdout.write("merge gates: " + ("FAILED" if problems else "passed") + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
