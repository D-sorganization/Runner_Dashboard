#!/usr/bin/env python3
"""Combine parallel coverage data and enforce ``[tool.coverage.report]`` (#4299)."""

from __future__ import annotations

import argparse
import glob
import re
import sys
import tomllib
from pathlib import Path

# Shard ids become a filename suffix. Reject path separators and empty values.
_SHARD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")


def discover_data_files(root: Path) -> list[Path]:
    """Return coverage data files under ``root`` (``.coverage`` and ``.coverage.*``)."""
    names = {root / ".coverage", *(Path(p) for p in glob.glob(str(root / ".coverage.*")))}
    return sorted(p for p in names if p.is_file())


def stage_shard_coverage(root: Path, shard: str) -> Path:
    """Publish one shard's combined data under a unique parallel-data name.

    Pre: ``shard`` matches ``[A-Za-z0-9][A-Za-z0-9._-]{0,31}``.
    Pre: ``root`` is a directory that contains a non-empty ``.coverage`` file,
    an already staged ``.coverage.shard<shard>`` file, or other ``.coverage.*``
    parallel data.
    Post: when a bare ``.coverage`` was present, ``.coverage.shard<shard>``
    exists, the bare file does not, and the return value is that staged path.
    A flat artifact download can then keep every shard: two files both named
    ``.coverage`` would otherwise overwrite each other.
    """
    if not isinstance(shard, str) or _SHARD_ID.fullmatch(shard) is None:
        raise ValueError(f"shard id must match {_SHARD_ID.pattern!r}, got {shard!r}")
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"coverage root is not a directory: {root}")
    dest = root / f".coverage.shard{shard}"
    source = root / ".coverage"
    if source.is_file():
        payload = source.read_bytes()
        if not payload:
            raise ValueError(f"coverage data file is empty: {source}")
        dest.write_bytes(payload)
        source.unlink()
        if source.exists() or not dest.is_file() or dest.stat().st_size == 0:
            raise RuntimeError(f"failed to stage coverage data at {dest}")
        return dest
    if dest.is_file():
        return dest
    parallel = discover_data_files(root)
    if parallel:
        return parallel[0]
    raise FileNotFoundError(f"No .coverage data file under {root} to stage for shard {shard}")


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
    parser.add_argument(
        "--stage-shard",
        metavar="SHARD",
        help=(
            "Copy .coverage to .coverage.shardSHARD and remove the bare file so a flat artifact merge keeps every shard"
        ),
    )
    args = parser.parse_args(argv)
    if args.stage_shard is not None:
        try:
            staged = stage_shard_coverage(args.root, args.stage_shard)
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(staged.name)
        return 0
    pyproject = args.pyproject if args.pyproject.is_absolute() else args.root / args.pyproject
    code, message = enforce(args.root.resolve(), pyproject.resolve())
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
