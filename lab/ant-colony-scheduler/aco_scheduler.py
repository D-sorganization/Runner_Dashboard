"""Lab experiment: can a swarm-intelligence pheromone trail pick faster
runner nodes than round-robin, using only recorded job-duration profiles?

Fuses swarm intelligence (ant colony optimization) with fleet scheduling.
This module never dispatches a real job; it only simulates node choice
against a fixture of recorded/synthetic per-node duration profiles.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

EVAPORATION_RATE = 0.10
REWARD_SCALE = 1.0


@dataclass(frozen=True)
class Node:
    name: str
    mean_duration_s: float
    std_duration_s: float


def load_nodes(fixture_path: Path) -> list[Node]:
    """Load node duration profiles from a fixture JSON file.

    Precondition: fixture_path exists and contains a non-empty "nodes" list.
    Postcondition: returns one Node per fixture entry, order preserved.
    """
    data = json.loads(fixture_path.read_text())
    raw_nodes = data.get("nodes", [])
    if not raw_nodes:
        raise ValueError("fixture must declare at least one node")
    return [
        Node(
            name=entry["name"],
            mean_duration_s=float(entry["mean_duration_s"]),
            std_duration_s=float(entry["std_duration_s"]),
        )
        for entry in raw_nodes
    ]


def _sample_duration(node: Node, rng: random.Random) -> float:
    return max(0.1, rng.gauss(node.mean_duration_s, node.std_duration_s))


def run_round_robin(
    nodes: list[Node], job_count: int, rng: random.Random
) -> tuple[list[str], list[float]]:
    """Precondition: nodes is non-empty, job_count >= 0."""
    assert nodes, "run_round_robin requires at least one node"
    assignments = [nodes[i % len(nodes)].name for i in range(job_count)]
    durations = [
        _sample_duration(nodes[i % len(nodes)], rng) for i in range(job_count)
    ]
    return assignments, durations


def run_random(
    nodes: list[Node], job_count: int, rng: random.Random
) -> tuple[list[str], list[float]]:
    """Precondition: nodes is non-empty, job_count >= 0."""
    assert nodes, "run_random requires at least one node"
    assignments = []
    durations = []
    for _ in range(job_count):
        node = rng.choice(nodes)
        assignments.append(node.name)
        durations.append(_sample_duration(node, rng))
    return assignments, durations


def _aco_steps(
    nodes: list[Node],
    job_count: int,
    rng: random.Random,
    evaporation: float,
):
    """Shared generator: yields (chosen_name, duration, pheromones) per step."""
    assert nodes, "_aco_steps requires at least one node"
    assert 0.0 < evaporation < 1.0, "evaporation must be in (0, 1)"

    pheromones = {node.name: 1.0 / len(nodes) for node in nodes}

    for _ in range(job_count):
        names = list(pheromones.keys())
        weights = [pheromones[name] for name in names]
        chosen_name = rng.choices(names, weights=weights, k=1)[0]
        node = next(n for n in nodes if n.name == chosen_name)

        duration = _sample_duration(node, rng)

        reward = REWARD_SCALE / duration
        for name in pheromones:
            pheromones[name] *= 1.0 - evaporation
        pheromones[chosen_name] += reward * evaporation

        total = sum(pheromones.values())
        pheromones = {name: level / total for name, level in pheromones.items()}

        yield chosen_name, duration, dict(pheromones)


def run_aco(
    nodes: list[Node],
    job_count: int,
    rng: random.Random,
    evaporation: float = EVAPORATION_RATE,
) -> tuple[list[str], list[float], dict[str, float]]:
    """Simulate ant-colony pheromone-trail node selection.

    Each simulated job is one "ant": it picks a node with probability
    proportional to that node's current pheromone level, observes a
    sampled job duration, deposits pheromone inversely proportional to
    that duration (faster completions reinforce more strongly), then all
    trails evaporate by `evaporation` before the next job.

    Precondition: nodes is non-empty, 0 < evaporation < 1, job_count >= 0.
    Postcondition: returned pheromone levels are non-negative and sum to 1.
    """
    assignments: list[str] = []
    durations: list[float] = []
    pheromones: dict[str, float] = {node.name: 1.0 / len(nodes) for node in nodes}

    for chosen_name, duration, snapshot in _aco_steps(
        nodes, job_count, rng, evaporation
    ):
        assignments.append(chosen_name)
        durations.append(duration)
        pheromones = snapshot

    return assignments, durations, pheromones


def run_aco_trace(
    nodes: list[Node],
    job_count: int,
    rng: random.Random,
    evaporation: float = EVAPORATION_RATE,
    sample_every: int = 5,
) -> list[dict[str, float]]:
    """Same simulation as run_aco, sampled every `sample_every` steps for
    visualization. Precondition: sample_every >= 1."""
    assert sample_every >= 1, "sample_every must be >= 1"
    trace: list[dict[str, float]] = []
    for step, (_chosen_name, _duration, snapshot) in enumerate(
        _aco_steps(nodes, job_count, rng, evaporation), start=1
    ):
        if step % sample_every == 0:
            trace.append({"step": step, **snapshot})
    return trace


if __name__ == "__main__":
    fixture = Path(__file__).parent / "fixtures" / "node_profiles.json"
    demo_nodes = load_nodes(fixture)
    demo_assignments, demo_durations, demo_pheromones = run_aco(
        demo_nodes, job_count=300, rng=random.Random(42)
    )
    print("Final pheromone levels:", demo_pheromones)
    print(
        "Share of jobs sent to each node:",
        {
            node.name: demo_assignments.count(node.name) / len(demo_assignments)
            for node in demo_nodes
        },
    )
