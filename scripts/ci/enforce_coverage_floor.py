#!/usr/bin/env python3
"""Combine parallel coverage data and enforce ``[tool.coverage.report]`` (#4299)."""

from __future__ import annotations

import argparse
import glob
import sys
import tomllib
from pathlib import Path


def discover_data_files(root: Path) -> list[Path]:
    """Return coverage data files under ``root`` (``.coverage`` and ``.coverage.*``)."""
    names = {root / ".coverage", *(Path(p) for p in glob.glob(str(root / ".coverage.*")))}
    return sorted(p for p in names if p.is_file())


def load_fail_under(pyproject: Path) -> float:
    report = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["coverage"]["report"]
    value = report.get("fail_under")
    if not isinstance(value, (int, float)):
        raise ValueError("pyproject.toml is missing [tool.coverage.report] fail_under")
    return float(value)


def enforce(root: Path, pyproject: Path) -> tuple[int, str]:
    """Combine discovered data files and fail when total coverage is below the floor."""
    coverage = __import__("coverage")
    data_files = discover_data_files(root)
    if not data_files:
        return 2, f"No coverage data files under {root}"

    target = root / ".coverage"
    if len(data_files) == 1:
        cov = coverage.Coverage(data_file=str(data_files[0]), config_file=str(pyproject))
    else:
        # coverage.combine data_paths are directories to scan for parallel data files
        # (``.coverage.*``), not individual file paths (#4299 / Codex review).
        if target.is_file():
            target.unlink()
        cov = coverage.Coverage(data_file=str(target), config_file=str(pyproject))
        cov.combine(data_paths=[str(root)], strict=True)
    cov.load()
    total = cov.report()
    floor = load_fail_under(pyproject)
    if total + 1e-9 < floor:
        return 1, f"Total coverage {total:.2f}% is below fail_under={floor:.0f}%"
    return 0, f"Total coverage {total:.2f}% meets fail_under={floor:.0f}%"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Directory containing .coverage* shard files (default: cwd)",
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="Path to pyproject.toml with [tool.coverage.report]",
    )
    args = parser.parse_args(argv)
    pyproject = args.pyproject if args.pyproject.is_absolute() else args.root / args.pyproject
    code, message = enforce(args.root.resolve(), pyproject.resolve())
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
