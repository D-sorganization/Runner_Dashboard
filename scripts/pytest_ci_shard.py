"""Select a pytest node-id shard for merge_group CI (epic #2886).

Pre: groups >= 1, index in 1..groups, marker expression is valid for pytest.
Post: prints one node id per line for the selected shard; exits 2 when empty.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from typing import TypeVar

import pytest

_T = TypeVar("_T")


def _parse_collected_node_ids(stdout: str) -> list[str]:
    nodes: list[str] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("="):
            break
        head = line.split("[", 1)[0]
        if "::" not in head or " " in head or head.endswith(":"):
            continue
        nodes.append(line)
    return nodes


def _collect_node_ids(marker_expr: str) -> list[str]:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=-q",
            "-W",
            "ignore",
            "--collect-only",
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
    return _parse_collected_node_ids(proc.stdout)


def select_shard(nodes: Sequence[_T], groups: int, index: int) -> list[_T]:
    if groups < 1:
        raise ValueError("groups must be >= 1")
    if index < 1 or index > groups:
        raise ValueError("index must be in 1..groups")
    return [node for offset, node in enumerate(nodes) if (offset % groups) + 1 == index]


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("ci_shard", "merge_group CI shard selection")
    group.addoption(
        "--ci-shard-groups",
        type=int,
        default=0,
        help="Total number of CI shards (disabled when <= 1).",
    )
    group.addoption(
        "--ci-shard-index",
        type=int,
        default=1,
        help="1-based index of the current CI shard (1..groups).",
    )


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    groups = int(config.getoption("--ci-shard-groups", default=0) or 0)
    if groups <= 1:
        return
    index = int(config.getoption("--ci-shard-index", default=1) or 1)
    selected = select_shard(items, groups, index)
    if items and not selected:
        raise pytest.UsageError("pytest shard selected zero tests")
    selected_ids = {id(item) for item in selected}
    deselected = [item for item in items if id(item) not in selected_ids]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item: pytest.Item) -> None:
    del item
    health_mod = sys.modules.get("runner_control_health")
    if health_mod is not None:
        reset = getattr(health_mod, "reset_runner_control_health", None)
        if callable(reset):
            reset()


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
