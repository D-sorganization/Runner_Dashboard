"""Select a pytest node-id shard for merge_group CI (epic #2886).

Pre: groups >= 1, index in 1..groups, marker expression is valid for pytest.
Post: prints one node id per line for the selected shard; exits 2 when empty.
"""

from __future__ import annotations

import argparse
import subprocess
import sys


def _collect_node_ids(marker_expr: str) -> list[str]:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "tests/",
            "-m",
            marker_expr,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 5):
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(proc.returncode or 1)
    nodes: list[str] = []
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("="):
            continue
        if line.endswith(" warnings summary"):
            continue
        nodes.append(line.split()[0])
    return nodes


def select_shard(nodes: list[str], groups: int, index: int) -> list[str]:
    if groups < 1:
        raise ValueError("groups must be >= 1")
    if index < 1 or index > groups:
        raise ValueError("index must be in 1..groups")
    return [node for offset, node in enumerate(nodes) if (offset % groups) + 1 == index]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List pytest node ids for one CI shard.")
    parser.add_argument("--groups", type=int, required=True)
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument(
        "--marker",
        default="not integration and not playwright and not slow",
        help="pytest -m expression (default: PR-tier fast suite)",
    )
    args = parser.parse_args(argv)
    nodes = _collect_node_ids(args.marker)
    selected = select_shard(nodes, args.groups, args.index)
    if not selected:
        print("::error::pytest shard selected zero tests", file=sys.stderr)
        return 2
    for node in selected:
        print(node)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
