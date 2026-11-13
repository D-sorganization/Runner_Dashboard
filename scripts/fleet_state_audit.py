#!/usr/bin/env python3
"""Audit a Barb-style markdown state table (#3640)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from staff.fleet_state_audit import default_cache_path, run_audit  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--path", required=True, type=Path)
    p.add_argument("--no-live", action="store_true")
    p.add_argument("--previous", type=Path, default=None)
    p.add_argument("--write-cache", action="store_true")
    args = p.parse_args(argv)
    if not args.path.is_file():
        print(f"missing state file: {args.path}", file=sys.stderr)
        return 2
    report = run_audit(
        args.path,
        live=not args.no_live,
        previous_path=args.previous or default_cache_path(args.path),
        write_cache=args.write_cache,
    )
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
