# Lab experiment: ant-colony pheromone trails for runner node picking

**Can we pick runner nodes with ant-colony pheromone trails?**
Fields fused: swarm intelligence + fleet scheduling.

Status: **open, instructive partial result**. Do not promote without a
second iteration (see "What would make this real" below).

## Question

The fleet already round-robins jobs across nodes of very different speed
(`ControlTower`, `DeskComputer`, `OGLaptop` per `GET /api/staff/summary`).
Ant colony optimization lets a population of "ants" collectively discover a
good path by depositing pheromone on the routes that worked, with unused
trails evaporating. Could the same mechanism let a job scheduler discover
which node is fastest purely from feedback, without hardcoding it?

## What was built

Everything lives in this directory and touches nothing else in the repo:

- `aco_scheduler.py` — the simulation: `run_round_robin`, `run_random`, and
  `run_aco` (pheromone-weighted node choice, reward = `1 / observed
  duration`, evaporation 0.10 per job), plus `run_aco_trace` for the
  visualization.
- `fixtures/node_profiles.json` — a **synthetic** fixture (not live fleet
  telemetry) modeling three nodes with different mean/std job durations,
  shaped after the real node names for readability.
- `test_aco_scheduler.py` — 7 pytest cases (TDD: written and run red before
  `aco_scheduler.py` existed), asserting pheromone levels stay a valid
  probability distribution, round-robin/random stay deterministic under a
  seed, and ACO's fastest-node share and mean duration.
- `generate_trace.py` — regenerates `fixtures/pheromone_trace.json`
  (deterministic, seed 42) for the visualization.
- `visualize.html` — a self-contained page (no build step, no dependency)
  that fetches the trace fixture and draws each node's pheromone share over
  simulated time on a canvas. Serve the folder locally to view it, e.g.
  `python3 -m http.server` from `lab/ant-colony-scheduler/`, since browsers
  block `fetch()` from `file://`.

Data sources: the fixture is synthetic; no live writes, no real dispatch,
no network calls beyond the local static file fetch in the browser.

## Result

Run: 300 simulated jobs, seed 42, evaporation 0.10, reward = `1/duration`.

| Strategy    | Mean job duration (s) | Share sent to fastest node (ControlTower) |
| ----------- | ---------------------: | ------------------------------------------: |
| Round-robin | 66.9                   | 33.3%                                       |
| Random      | 61.9                   | 33.3%                                       |
| ACO         | 64.7                   | 33.7%                                       |

Final pheromone levels: `ControlTower 0.387, DeskComputer 0.336, OGLaptop
0.277` — a real but modest lean toward the fastest node.

**The instructive part:** ACO beat round-robin by only ~3.5%, and lost to
plain random on this run. The reward signal is a single noisy sample
(`1 / duration`) with no smoothing, and `OGLaptop`'s high variance
(std 30s vs. mean 95s) means it occasionally posts a lucky fast run that
gets over-rewarded, propping its pheromone back up right as it starts to
decay. Ant colony optimization converges cleanly when the environment is
mostly static and the reward reflects many trips per edge; a single-sample,
high-variance reward per "trip" is a different regime, and 300 jobs was not
enough to average out the noise. That mismatch, not a bug, is the finding.

## What would make this real

Not built here (would exceed the $5/run budget as a first cut):

- Reward from a smoothed rolling mean/EWMA of recent durations per node
  instead of one raw sample, to damp the noise that stalled convergence.
- A much longer simulation (or replaying real historical run durations from
  the Runner Dashboard's own run history) to see if pheromone bias keeps
  growing past 300 jobs or plateaus for a structural reason.
- A queueing-aware reward (penalize nodes that are already busy), since the
  real scheduling question is about instantaneous availability, not just
  historical speed.

## Lab safety

No fleet dispatch touched. No live writes. No new dependency (stdlib only:
`json`, `random`, `dataclasses`, `pathlib`; the page uses vanilla JS/canvas).
Everything stays inside this directory.
