# Benchmarks

Performance benchmarks for the runner-dashboard backend.

## Running

```bash
cd ..
python -m benchmarks.health_endpoint
```

## Poll-cycle benchmark

`bench_poll_cycle.py` runs the real app in-process against stubbed GitHub REST and a fake `gh`
(no network, no real token); per polled endpoint, cold and warm, it reports wall time, GitHub
HTTP calls, `gh` / other spawns and the longest event-loop stall.

```bash
python -m benchmarks.bench_poll_cycle [--latency 0.15] [--mode token|gh] [--endpoint EP]... [--json-out F]
```

Numbers are **indicative only** (stubbed GitHub, whatever box runs it), not production timings;
compare runs on one machine.

## Adding Benchmarks

Place new benchmark scripts in this directory. Use `timeit` or `pytest-benchmark`
for consistent measurements.
