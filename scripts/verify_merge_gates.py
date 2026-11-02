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
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
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


def resolve_bash(
    bash_path: str | None = None,
    *,
    platform: str = sys.platform,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve a deterministic bash executable for running contract scripts."""
    if bash_path:
        p = Path(bash_path)
        if p.is_file():
            return str(p.resolve())
        which = shutil.which(bash_path)
        if which:
            return which
        raise FileNotFoundError(f"Specified bash executable not found: {bash_path}")

    env = os.environ if environ is None else environ
    for var in ("RD_BASH", "BASH"):
        val = env.get(var)
        if val:
            p = Path(val)
            if p.is_file():
                return str(p.resolve())
            which = shutil.which(val)
            if which:
                return which
            return val

    if platform == "win32":
        git_which = shutil.which("git")
        if git_which:
            try:
                git_root = Path(git_which).resolve().parents[1]
                for candidate in (git_root / "bin" / "bash.exe", git_root / "usr" / "bin" / "bash.exe"):
                    if candidate.is_file():
                        return str(candidate)
            except Exception:
                pass

        for pf_var in ("ProgramFiles", "ProgramFiles(x86)"):
            pf = env.get(pf_var)
            if pf:
                for candidate in (
                    Path(pf) / "Git" / "bin" / "bash.exe",
                    Path(pf) / "Git" / "usr" / "bin" / "bash.exe",
                ):
                    if candidate.is_file():
                        return str(candidate)

        local_app_data = env.get("LOCALAPPDATA")
        if local_app_data:
            candidate = Path(local_app_data) / "Programs" / "Git" / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)

        which_bash = shutil.which("bash")
        if which_bash and "system32" not in which_bash.lower():
            return which_bash

        return "bash"

    return "bash"


def parse_api_response(response: Any) -> dict[str, Any]:
    """Parse JSON from an API / TestClient response with actionable diagnostics.

    Surfaces status code and content type before parsing JSON, without
    printing credentials or response contents.
    """
    status = getattr(response, "status_code", None)
    headers = getattr(response, "headers", {}) or {}
    content_type = headers.get("content-type", "") if hasattr(headers, "get") else ""

    if status is not None and status != 200:
        raise ValueError(f"API response failed with status {status} (content-type: {content_type!r})")
    if content_type and not ("application/json" in content_type or "+json" in content_type):
        raise ValueError(f"API response has non-JSON content-type: {content_type!r} (status: {status})")

    try:
        if callable(getattr(response, "json", None)):
            return response.json()
        raw = getattr(response, "text", "")
        return json.loads(raw)
    except Exception as exc:
        err_type = type(exc).__name__
        msg = f"Failed to decode API JSON response (status: {status}, content-type: {content_type!r}): {err_type}"
        raise ValueError(msg) from None


def check_contract(
    root: Path,
    paths: Sequence[str],
    run: Runner = subprocess.run,
    *,
    bash_path: str | None = None,
    python_path: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    if not touches_api_contract(paths):
        return []
    resolved_bash = resolve_bash(bash_path, environ=environ)
    command = [resolved_bash, "scripts/gen-api-client.sh", "--check"]
    env = dict(os.environ if environ is None else environ)
    py = python_path or env.get("PYTHON") or sys.executable
    env["PYTHON"] = py
    proc = run(command, cwd=root, capture_output=True, text=True, check=False, env=env)
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
    parser.add_argument("--bash", help="Bash executable for API contract check")
    parser.add_argument("--python", help="Python interpreter for API contract check")
    args = parser.parse_args(argv)
    root = args.repository_root.resolve()
    problems: list[str] = []
    if args.only != "baseline":
        problems += check_contract(
            root,
            changed_paths(root, args.base),
            bash_path=args.bash,
            python_path=args.python,
        )
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
