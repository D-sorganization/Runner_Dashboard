# Runner observability metrics

Issue [#651](https://github.com/D-sorganization/Runner_Dashboard/issues/651)
exposed a class of failure where the nightly `runner-cleanup.service` had
been silently failing for months on busy hosts because of a strict-mode bug
([fix in #660](https://github.com/D-sorganization/Runner_Dashboard/pull/660)).
The only visible signal was that CI kept failing on PR after PR; nothing
told us cleanup itself had stopped running.

This document describes the three Prometheus metrics added so that silent
regression can't happen again. Each metric has a single, well-defined scrape
path — there is no ambiguity about which subsystem owns the data.

## 1. `runner_cleanup_runs_total`

| Field       | Value                                                          |
| ----------- | -------------------------------------------------------------- |
| Type        | Counter                                                        |
| Labels      | `host`, `result` ∈ `{ok, skipped, failed}`                     |
| Emitter     | `deploy/runner-cleanup.sh` (`write_metrics()` in an EXIT trap) |
| Scrape path | node_exporter textfile collector                               |
| State file  | `${TEXTFILE_COLLECTOR_DIR}/runner_cleanup.prom`                |
| Default dir | `/var/lib/node_exporter/textfile_collector`                    |
| Cadence     | Once per cleanup pass (daily timer, plus manual invocations)   |

Co-emitted from the same trap:

- `runner_cleanup_stop_failures_total{host}` — counter, number of runner
  units that could not be stopped during the most recent pass. A non-zero
  reading means cleanup ran but elided one or more runners.
- `runner_cleanup_last_run_timestamp_seconds{host}` — gauge, unix time of
  the most recent pass. Stale-by-time alerting is more reliable than
  stale-by-counter, since a perpetually-skipped run still increments
  `result="skipped"`.

### Alerting

```yaml
- alert: RunnerCleanupStale
  expr: time() - runner_cleanup_last_run_timestamp_seconds > 60 * 60 * 36
  for: 15m
  annotations:
    summary: "runner-cleanup hasn't completed on {{ $labels.host }} for >36h"

- alert: RunnerCleanupFailing
  expr: increase(runner_cleanup_runs_total{result="failed"}[2h]) > 0
  for: 5m
```

### How this would have caught #651

The strict-mode regression caused `runner-cleanup.sh` to exit early under
`set -Eeuo pipefail` whenever a single runner's directory was unreadable.
The EXIT trap fires regardless of return value, so the textfile would have
emitted `result="failed"` continuously. The `RunnerCleanupFailing` alert
would have paged on the first 2-hour window.

## 2. `runner_corruption_residue_count`

| Field       | Value                                                                                       |
| ----------- | ------------------------------------------------------------------------------------------- |
| Type        | Gauge                                                                                       |
| Labels      | `host`, `runner`, `kind` ∈ `{file_commands, diag_pages}`                                    |
| Emitter     | `deploy/runner-corruption-scan.sh`                                                          |
| Scrape path | node_exporter textfile collector                                                            |
| State file  | `${PROM_FILE}` (default `/var/lib/node_exporter/textfile_collector/runner_corruption.prom`) |
| Schedule    | `runner-corruption-scan.timer` — `OnUnitActiveSec=5min`                                     |

The scan walks every `runner-*` directory under `$RUNNER_ROOT` and counts:

- **`kind="file_commands"`** — every file under
  `<runner>/_work/_temp/_runner_file_commands/`. By contract this directory
  is empty between jobs; any file is residue from a mid-job kill and will
  break the next allocation with
  `Missing file at path: .../_runner_file_commands/save_state_<uuid>`.

- **`kind="diag_pages"`** — `.log` files directly under
  `<runner>/_diag/pages/` older than `DIAG_PAGES_MIN_AGE_DAYS` (default 1).
  When two runs collide on a UUID the runner aborts with
  `the file '.../<uuid>_<uuid>_1.log' already exists`.

### Alerting

```yaml
- alert: RunnerCorruptionResidue
  expr: sum by (host, runner) (runner_corruption_residue_count) > 0
  for: 10m
```

The 10-minute `for:` is long enough to span a busy job that legitimately
populates `_runner_file_commands` mid-execution.

### How this would have caught #651

The corruption residue is the _physical_ failure mode that surfaces when
cleanup stops running. Even if `runner_cleanup_runs_total` hadn't existed,
this metric would have shown stale residue accumulating per-runner. Alerts
would have fired on the first runner to hold ≥1 residue file for >10 min.

## 3. `runner_orphan_worker_total`

| Field       | Value                                                     |
| ----------- | --------------------------------------------------------- |
| Type        | Counter                                                   |
| Labels      | `host`, `runner` (systemd unit name)                      |
| Emitter     | Vector journald → `log_to_metric` transform               |
| Scrape path | Vector Prometheus exporter sink                           |
| Endpoint    | `${VECTOR_PROM_ADDR:-0.0.0.0:9598}/metrics`               |
| Loki audit  | Loki stream `{event="orphan_worker"}` (raw journald line) |

Pattern matched in journald:

```
Unit process <PID> (Runner.Worker) remains running after unit stopped
```

This message is emitted by systemd when an `actions.runner.*.service` unit
with `KillMode=process` stops without taking its `Runner.Worker` child with
it. The orphan continues writing to `_runner_file_commands/` and
`_diag/pages/`, producing the residue counted by metric #2.

### Open coupling

The `KillMode=` policy on the per-runner systemd units is owned by the
autoscaler PR (#3 in this series) — see the parent task. This metric is
_observation only_ and intentionally avoids touching unit files.

> **TODO (operator):** confirm your Prometheus scraper is configured to
> pull `<vector-host>:9598/metrics`. The fleet does not yet ship a
> centralised Prometheus config in this repo; if Vector is behind a
> Tailscale tailnet, expose the scrape port via Tailscale, not the public
> internet.

### Alerting

```yaml
- alert: RunnerOrphanWorker
  expr: increase(runner_orphan_worker_total[15m]) > 0
  for: 5m
  annotations:
    summary: "Orphan Runner.Worker detected on {{ $labels.host }} ({{ $labels.runner }})"
```

## Installation summary

`deploy/install-runner-maintenance.sh` installs the cleanup script, the
corruption-scan script, and three systemd timers:

| Timer                          | Cadence                | Emits                                   |
| ------------------------------ | ---------------------- | --------------------------------------- |
| `runner-cleanup.timer`         | Daily, 04:20 + jitter  | `runner_cleanup_runs_total` family      |
| `runner-corruption-scan.timer` | `OnUnitActiveSec=5min` | `runner_corruption_residue_count`       |
| `runner-scheduler.timer`       | Every 5 min            | (pre-existing — not changed by this PR) |

Vector (`deploy/observability/vector.toml`) handles the orphan-Worker
metric; install Vector separately on each fleet node.

The textfile-collector directory is operator-configurable via the
`TEXTFILE_COLLECTOR_DIR` env var; the default
(`/var/lib/node_exporter/textfile_collector`) matches the upstream
node_exporter default.
