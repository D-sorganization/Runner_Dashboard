"""Tests for the ant-colony pheromone-trail node scheduler lab experiment.

Written before aco_scheduler.py existed (TDD red step); the assertions below
are the acceptance criteria the implementation must satisfy.
"""

from __future__ import annotations

import json
import random
import statistics
from pathlib import Path

import pytest

from aco_scheduler import (
    Node,
    load_nodes,
    run_aco,
    run_aco_trace,
    run_random,
    run_round_robin,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "node_profiles.json"


def test_load_nodes_reads_fixture() -> None:
    nodes = load_nodes(FIXTURE_PATH)
    assert [n.name for n in nodes] == ["ControlTower", "DeskComputer", "OGLaptop"]
    assert all(isinstance(n, Node) for n in nodes)
    assert nodes[0].mean_duration_s < nodes[2].mean_duration_s


def test_load_nodes_rejects_empty_list(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"nodes": []}))
    with pytest.raises(ValueError, match="at least one node"):
        load_nodes(empty)


def test_round_robin_cycles_nodes_in_order() -> None:
    nodes = load_nodes(FIXTURE_PATH)
    assignments, _durations = run_round_robin(nodes, job_count=6, rng=random.Random(1))
    assert assignments == [
        "ControlTower",
        "DeskComputer",
        "OGLaptop",
        "ControlTower",
        "DeskComputer",
        "OGLaptop",
    ]


def test_random_and_round_robin_are_deterministic_under_seed() -> None:
    nodes = load_nodes(FIXTURE_PATH)
    a1, d1 = run_random(nodes, job_count=50, rng=random.Random(7))
    a2, d2 = run_random(nodes, job_count=50, rng=random.Random(7))
    assert a1 == a2
    assert d1 == d2


def test_aco_pheromone_levels_sum_to_one() -> None:
    nodes = load_nodes(FIXTURE_PATH)
    _assignments, _durations, pheromones = run_aco(
        nodes, job_count=200, rng=random.Random(3)
    )
    assert pheromones.keys() == {n.name for n in nodes}
    assert statistics.fmean(pheromones.values()) == pytest.approx(1.0 / len(nodes))
    assert sum(pheromones.values()) == pytest.approx(1.0)


def test_aco_converges_toward_the_faster_node() -> None:
    """The core hypothesis: pheromone reinforcement should bias job
    placement toward the historically-faster node more than a naive
    round-robin split, without ever excluding the slower nodes."""
    nodes = load_nodes(FIXTURE_PATH)
    job_count = 300
    aco_assignments, aco_durations, pheromones = run_aco(
        nodes, job_count=job_count, rng=random.Random(42)
    )

    fastest = min(nodes, key=lambda n: n.mean_duration_s).name
    aco_fastest_share = aco_assignments.count(fastest) / job_count
    round_robin_share = 1.0 / len(nodes)

    assert aco_fastest_share > round_robin_share
    assert pheromones[fastest] == max(pheromones.values())
    # exploration never fully collapses to a single node
    assert all(count > 0 for count in _counts(aco_assignments, nodes))

    rr_assignments, rr_durations = run_round_robin(
        nodes, job_count=job_count, rng=random.Random(42)
    )
    assert statistics.fmean(aco_durations) < statistics.fmean(rr_durations)


def test_aco_trace_samples_at_fixed_interval() -> None:
    nodes = load_nodes(FIXTURE_PATH)
    trace = run_aco_trace(nodes, job_count=20, rng=random.Random(5), sample_every=5)
    assert [point["step"] for point in trace] == [5, 10, 15, 20]
    for point in trace:
        levels = [point[n.name] for n in nodes]
        assert sum(levels) == pytest.approx(1.0)


def _counts(assignments: list[str], nodes: list[Node]) -> list[int]:
    return [assignments.count(n.name) for n in nodes]
