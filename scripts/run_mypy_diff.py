#!/usr/bin/env python3
# Vendored from Repository_Management shared_scripts/run_mypy_diff.py
# (commit ed046ebed886cda38796704aa57a06f7081e52c9).
# Re-sync from upstream; do not fork. Local changes: imports use `scripts.`
# instead of `shared_scripts.`, and ruff format at this repo's line length.
"""Run diff-scoped mypy type checks on changed Python files.

Determines the scope of changed/added files between the current branch and its
base (e.g. origin/main, FLEET_HOOK_FROM_REF..FLEET_HOOK_TO_REF, staged files,
or @{upstream}..HEAD), filters for existing Python source files, and runs mypy
with --ignore-missing-imports and --follow-imports=silent.

This ensures pre-push and CI hooks only fail on type errors introduced in the
active change set rather than surfacing unrelated pre-existing repository debt.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

logger = logging.getLogger("run_mypy_diff")

DEFAULT_TARGET_PREFIXES = (
    "src/",
    "python/",
    "scripts/",
    "shared_scripts/",
    "app/",
    "backend/",
)
STANDARD_EXCLUDE_PATTERNS = (
    r"(?:^|/)(?:archive|legacy|experimental|matlab|javascript|__pycache__|\.pytest_cache|\.mypy_cache|\.venv|venv|node_modules|\.git|\.github|build|dist|third_party|Claude_Skills|agent_templates)(?:/|$)",
)
TEST_PATTERNS = (r"(?:^|/)tests?(?:/|$)",)
BASE_CANDIDATES = ("origin/main", "origin/HEAD", "main", "@{upstream}")


def _setup_logging(verbose: bool = False) -> None:
    """Configure logging handler if not already configured."""
    level = logging.DEBUG if verbose else logging.INFO
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)


def _run_git(args: Sequence[str], cwd: Path | None = None) -> list[str]:
    """Execute a git command and return stripped non-empty lines."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or Path.cwd(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (subprocess.SubprocessError, OSError):
        return []


def resolve_merge_base(base_candidate: str, head_ref: str = "HEAD", cwd: Path | None = None) -> str | None:
    """Find merge-base SHA between head_ref and candidate base."""
    lines = _run_git(["merge-base", head_ref, base_candidate], cwd=cwd)
    return lines[0] if lines else None


def resolve_changed_files(
    from_ref: str | None = None,
    to_ref: str | None = None,
    base_ref: str | None = None,
    cwd: Path | None = None,
) -> list[str]:
    """Discover changed files using diff-scoped ref resolution.

    Priority order:
    1. Explicit from_ref..to_ref or FLEET_HOOK_FROM_REF..FLEET_HOOK_TO_REF env vars.
    2. Staged changes (git diff --cached).
    3. Working tree changes vs HEAD (git diff HEAD).
    4. Base ref merge-base (e.g. origin/main..HEAD).
    5. Fallback commit range (HEAD~1..HEAD).
    """
    root = cwd or Path.cwd()
    env_from = from_ref or os.environ.get("FLEET_HOOK_FROM_REF")
    env_to = to_ref or os.environ.get("FLEET_HOOK_TO_REF")

    if env_from and env_to:
        files = _run_git(
            ["diff", "--name-only", "--diff-filter=ACMR", f"{env_from}..{env_to}"],
            cwd=root,
        )
        if files:
            return sorted(set(files))

    staged = _run_git(["diff", "--cached", "--name-only", "--diff-filter=ACMR"], cwd=root)
    if staged:
        return sorted(set(staged))

    working = _run_git(["diff", "HEAD", "--name-only", "--diff-filter=ACMR"], cwd=root)
    if working:
        return sorted(set(working))

    candidates: list[str] = [base_ref] if base_ref else list(BASE_CANDIDATES)
    for candidate in candidates:
        base = resolve_merge_base(candidate, "HEAD", cwd=root)
        if base:
            diff_files = _run_git(
                ["diff", "--name-only", "--diff-filter=ACMR", f"{base}..HEAD"],
                cwd=root,
            )
            if diff_files:
                return sorted(set(diff_files))

    for candidate in candidates:
        diff_files = _run_git(
            ["diff", "--name-only", "--diff-filter=ACMR", f"{candidate}...HEAD"],
            cwd=root,
        )
        if diff_files:
            return sorted(set(diff_files))

    head_fallback = _run_git(["diff", "--name-only", "--diff-filter=ACMR", "HEAD~1..HEAD"], cwd=root)
    if head_fallback:
        return sorted(set(head_fallback))

    return []


def filter_python_files(
    files: Iterable[str],
    root: Path | None = None,
    target_prefixes: Sequence[str] | None = None,
    exclude_patterns: Sequence[str] | None = None,
    include_tests: bool = False,
) -> list[str]:
    """Filter files to existing Python source files within target scope."""
    base_dir = root or Path.cwd()
    compiled_standard = [re.compile(p) for p in STANDARD_EXCLUDE_PATTERNS]
    compiled_tests = [re.compile(p) for p in TEST_PATTERNS]
    compiled_custom = [re.compile(p) for p in (exclude_patterns or ())]

    matched: list[str] = []
    for file_path in files:
        posix_path = file_path.replace("\\", "/").lstrip("./")
        if not posix_path.endswith(".py"):
            continue

        disk_path = base_dir / posix_path
        if not disk_path.is_file():
            continue

        if not include_tests and any(cp.search(posix_path) for cp in compiled_tests):
            continue

        if any(cp.search(posix_path) for cp in compiled_standard):
            continue

        if any(cp.search(posix_path) for cp in compiled_custom):
            continue

        if target_prefixes:
            if not any(posix_path.startswith(prefix) for prefix in target_prefixes):
                continue
        elif target_prefixes is None and (base_dir / "src").is_dir():
            # If repo has src/ directory, default to scope within src/
            if not posix_path.startswith("src/"):
                continue

        matched.append(posix_path)

    return sorted(matched)


def resolve_mypypath(root: Path) -> str:
    """Build MYPYPATH search path for resolving imports across repo packages."""
    if "MYPYPATH" in os.environ:
        return os.environ["MYPYPATH"]

    candidates = ("src", "src/python/src", "python", ".")
    found = [str((root / candidate).resolve()) for candidate in candidates if (root / candidate).is_dir()]
    return os.pathsep.join(found) if found else ""


def build_mypy_cmd(
    files: Sequence[str],
    config_file: str | None = None,
    follow_imports: str = "silent",
    extra_args: Sequence[str] | None = None,
    root: Path | None = None,
) -> list[str]:
    """Construct mypy command line arguments."""
    base_dir = root or Path.cwd()
    cmd = [
        sys.executable,
        "-m",
        "mypy",
        "--ignore-missing-imports",
        f"--follow-imports={follow_imports}",
    ]
    if config_file:
        cmd.extend(["--config-file", config_file])
    elif (base_dir / "mypy.ini").is_file():
        cmd.extend(["--config-file", "mypy.ini"])

    if extra_args:
        cmd.extend(extra_args)

    cmd.extend(files)
    return cmd


def run_mypy_diff(
    files: Sequence[str] | None = None,
    from_ref: str | None = None,
    to_ref: str | None = None,
    base_ref: str | None = None,
    target_prefixes: Sequence[str] | None = None,
    exclude_patterns: Sequence[str] | None = None,
    include_tests: bool = False,
    config_file: str | None = None,
    follow_imports: str = "silent",
    extra_args: Sequence[str] | None = None,
    root: Path | None = None,
) -> int:
    """Execute diff-scoped mypy type check."""
    base_dir = root or Path.cwd()

    candidate_files = (
        list(files)
        if files
        else resolve_changed_files(
            from_ref=from_ref,
            to_ref=to_ref,
            base_ref=base_ref,
            cwd=base_dir,
        )
    )

    active_target_prefixes = target_prefixes
    if active_target_prefixes is None and files:
        active_target_prefixes = ()

    filtered = filter_python_files(
        candidate_files,
        root=base_dir,
        target_prefixes=active_target_prefixes,
        exclude_patterns=exclude_patterns,
        include_tests=include_tests,
    )

    if not filtered:
        logger.info("No changed Python files to type-check.")
        return 0

    logger.info(f"Running diff-scoped mypy on {len(filtered)} file(s)...")
    for path in filtered:
        logger.info(f"  - {path}")

    cmd = build_mypy_cmd(
        files=filtered,
        config_file=config_file,
        follow_imports=follow_imports,
        extra_args=extra_args,
        root=base_dir,
    )

    env = os.environ.copy()
    mypypath = resolve_mypypath(base_dir)
    if mypypath:
        env["MYPYPATH"] = mypypath

    try:
        result = subprocess.run(
            cmd,
            cwd=base_dir,
            env=env,
            check=False,
        )
        return result.returncode
    except FileNotFoundError as e:
        logger.error(f"Failed to execute mypy: {e}")
        return 1
    except OSError as e:
        logger.error(f"Execution error while running mypy: {e}")
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entrypoint."""
    parser = argparse.ArgumentParser(description="Run diff-scoped mypy on changed Python files.")
    parser.add_argument(
        "files",
        nargs="*",
        help=("Optional explicit list of files to check. If omitted, diff is resolved from Git."),
    )
    parser.add_argument(
        "--from-ref",
        help="Starting git ref for diff range (overrides FLEET_HOOK_FROM_REF).",
    )
    parser.add_argument(
        "--to-ref",
        help="Ending git ref for diff range (overrides FLEET_HOOK_TO_REF).",
    )
    parser.add_argument(
        "--base-ref",
        help="Target branch or base ref to diff against (e.g. origin/main).",
    )
    parser.add_argument(
        "--target-dirs",
        help=("Comma-separated prefixes of target directories to check (e.g. 'src/,python/')."),
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help=("Regex pattern of files/directories to exclude (can be specified multiple times)."),
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Include test files in type checking.",
    )
    parser.add_argument(
        "--config-file",
        help="Path to mypy configuration file.",
    )
    parser.add_argument(
        "--follow-imports",
        default="silent",
        choices=["silent", "skip", "normal", "error"],
        help="Mypy follow-imports behavior (default: silent).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose debug logging.",
    )

    args, unknown = parser.parse_known_args(argv)
    _setup_logging(args.verbose)

    target_prefixes = (
        [prefix.strip() for prefix in args.target_dirs.split(",") if prefix.strip()] if args.target_dirs else None
    )

    return run_mypy_diff(
        files=args.files or None,
        from_ref=args.from_ref,
        to_ref=args.to_ref,
        base_ref=args.base_ref,
        target_prefixes=target_prefixes,
        exclude_patterns=args.exclude,
        include_tests=args.include_tests,
        config_file=args.config_file,
        follow_imports=args.follow_imports,
        extra_args=unknown,
    )


if __name__ == "__main__":
    raise SystemExit(main())
