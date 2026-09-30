"""Regenerates fixtures/pheromone_trace.json for visualize.html.

Deterministic (fixed seed) so the committed fixture is reproducible;
re-run this script if aco_scheduler.py's algorithm changes.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from aco_scheduler import load_nodes, run_aco_trace

HERE = Path(__file__).parent


def main() -> None:
    nodes = load_nodes(HERE / "fixtures" / "node_profiles.json")
    trace = run_aco_trace(nodes, job_count=300, rng=random.Random(42), sample_every=5)
    out = {"nodes": [n.name for n in nodes], "trace": trace}
    (HERE / "fixtures" / "pheromone_trace.json").write_text(json.dumps(out, indent=2))
    print(f"wrote {len(trace)} trace points")


if __name__ == "__main__":
    main()
