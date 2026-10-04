#!/usr/bin/env python3
# Vendored from Repository_Management shared_scripts/run_pytest_diff.py
# (commit ed046ebed886cda38796704aa57a06f7081e52c9).
# Re-sync from upstream; do not fork. Local changes: imports use `scripts.`
# instead of `shared_scripts.`, and ruff format at this repo's line length.
"""Run diff-scoped pytest unit tests on mapped test files.

Determines the scope of changed/added files between the active branch and its
base (e.g. origin/main, FLEET_HOOK_FROM_REF..FLEET_HOOK_TO_REF, staged files,
or @{upstream}..HEAD), maps changed Python source files and direct test files
to corresponding pytest test targets, and runs pytest on that scoped subset.

Includes `-p no:pytestqt` to prevent Windows DLL load / INTERNALERROR failures
on environments where Qt bindings are unavailable or headless.
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

from scripts.run_mypy_diff import resolve_changed_files

logger = logging.getLogger("run_pytest_diff")

TEST_DIR_PATTERNS = ("tests/", "test/")
TEST_PREFIXES = ("test_",)
TEST_SUFFIXES = ("_test.py", "test.py")
STANDARD_TEST_ROOTS = ("tests", "src/python/tests", "tests/unit")


def _setup_logging(verbose: bool = False) -> None:
    """Configure logging handler if not already configured."""
    level = logging.DEBUG if verbose else logging.INFO
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)


def is_test_file(path_str: str) -> bool:
    """Check if a path corresponds to a test file."""
    normalized = path_str.replace("\\", "/")
    if not normalized.endswith(".py"):
        return False
    name = Path(normalized).name
    if any(name.startswith(p) for p in TEST_PREFIXES) or any(name.endswith(s) for s in TEST_SUFFIXES):
        return True
    return any(part in ("tests", "test") for part in Path(normalized).parts)


def _candidate_test_paths(rel_path: str) -> list[str]:
    """Generate candidate test file relative paths for a Python source file."""
    norm = rel_path.replace("\\", "/")
    p = Path(norm)
    stem = p.stem

    candidates: list[str] = [
        f"tests/test_{stem}.py",
        f"tests/{stem}_test.py",
        f"src/python/tests/test_{stem}.py",
        f"src/python/tests/{stem}_test.py",
        f"tests/unit/test_{stem}.py",
        f"tests/unit/{stem}_test.py",
        str(p.parent / f"test_{stem}.py"),
        str(p.parent / f"{stem}_test.py"),
    ]

    parts = list(p.parts)
    # Strip common leading source roots to test mirrored subdirectories
    while parts and parts[0] in ("src", "python", "shared_scripts", "scripts"):
        parts.pop(0)

    if parts:
        sub_dir = "/".join(parts[:-1])
        if sub_dir:
            candidates.extend(
                [
                    f"tests/{sub_dir}/test_{stem}.py",
                    f"tests/{sub_dir}/{stem}_test.py",
                    f"src/python/tests/{sub_dir}/test_{stem}.py",
                    f"tests/unit/{sub_dir}/test_{stem}.py",
                ]
            )

    return [c.replace("\\", "/") for c in candidates]


def resolve_test_targets(
    files: Iterable[str],
    fallback: str | None = None,
    root: Path | None = None,
) -> list[str]:
    """Map changed files to existing test targets under root."""
    base_root = root or Path.cwd()
    targets: set[str] = set()
    unmapped_python_sources: list[str] = []

    for file_str in files:
        norm = file_str.replace("\\", "/").strip()
        if not norm or not norm.endswith(".py"):
            continue

        full_path = base_root / norm
        if not full_path.exists():
            continue

        if is_test_file(norm):
            targets.add(norm)
            continue

        # Source python file: search candidate test files
        candidates = _candidate_test_paths(norm)
        matched = False
        for cand in candidates:
            if (base_root / cand).is_file():
                targets.add(cand)
                matched = True

        if not matched:
            # Check for glob match in standard test directories
            for test_root in STANDARD_TEST_ROOTS:
                tr_path = base_root / test_root
                if tr_path.is_dir():
                    for match in tr_path.rglob(f"test_*{Path(norm).stem}*.py"):
                        try:
                            rel_match = match.relative_to(base_root).as_posix()
                            targets.add(rel_match)
                            matched = True
                        except ValueError:
                            continue

        if not matched:
            unmapped_python_sources.append(norm)

    if not targets and unmapped_python_sources and fallback:
        fb_path = base_root / fallback.replace("\\", "/")
        if fb_path.exists():
            targets.add(fallback.replace("\\", "/"))

    return sorted(targets)


def build_pytest_cmd(
    targets: Sequence[str],
    extra_args: Sequence[str] | None = None,
    root: Path | None = None,
) -> list[str]:
    """Build the pytest command invocation with anti-crash flags."""
    cmd = [sys.executable, "-m", "pytest", *targets]

    # Guard against Qt crashes/DLL errors on Windows or headless runs
    cmd.extend(["-p", "no:pytestqt"])

    if extra_args:
        cmd.extend(extra_args)

    return cmd


def run_pytest_diff(
    files: Sequence[str] | None = None,
    from_ref: str | None = None,
    to_ref: str | None = None,
    base_ref: str | None = None,
    fallback: str | None = None,
    extra_args: Sequence[str] | None = None,
    root: Path | None = None,
    verbose: bool = False,
) -> int:
    """Run pytest scoped to tests mapped from changed files."""
    _setup_logging(verbose)
    base_root = root or Path.cwd()

    if files is None:
        changed = resolve_changed_files(
            from_ref=from_ref,
            to_ref=to_ref,
            base_ref=base_ref,
            cwd=base_root,
        )
    else:
        changed = list(files)

    targets = resolve_test_targets(changed, fallback=fallback, root=base_root)
    if not targets:
        logger.info("No test files mapped from changed files; skipping pytest.")
        return 0

    cmd = build_pytest_cmd(targets, extra_args=extra_args, root=base_root)
    logger.info("Running diff-scoped pytest: %s", " ".join(cmd))

    env = dict(os.environ)
    exe_dir = str(Path(sys.executable).parent)
    current_path = env.get("PATH", "")
    if exe_dir not in current_path.split(os.pathsep):
        env["PATH"] = f"{exe_dir}{os.pathsep}{current_path}" if current_path else exe_dir

    result = subprocess.run(cmd, cwd=base_root, env=env, check=False)
    return result.returncode


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for run_pytest_diff."""
    parser = argparse.ArgumentParser(description="Run diff-scoped pytest on mapped test targets.")
    parser.add_argument("--from-ref", help="Start git ref for diff range.")
    parser.add_argument("--to-ref", help="End git ref for diff range.")
    parser.add_argument("--base-ref", help="Base candidate ref (e.g. origin/main).")
    parser.add_argument(
        "--fallback",
        help=("Fallback test directory if python files changed without direct test mapping."),
    )
    parser.add_argument(
        "--pytest-args",
        help="Extra flags to pass to pytest (e.g. '-x -q --tb=short').",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output.")
    parser.add_argument(
        "files",
        nargs="*",
        help="Explicit list of changed files (skips git diff detection).",
    )

    args = parser.parse_args(argv)
    extra_args = shlex.split(args.pytest_args) if args.pytest_args else None

    return run_pytest_diff(
        files=args.files or None,
        from_ref=args.from_ref,
        to_ref=args.to_ref,
        base_ref=args.base_ref,
        fallback=args.fallback,
        extra_args=extra_args,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    raise SystemExit(main())
