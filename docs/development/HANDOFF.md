# Runner Dashboard Handoff

## Current Work

- Issue: [#1105](https://github.com/D-sorganization/Runner_Dashboard/issues/1105)
- Branch: `fix/issue-1105-runner-busy-detection`
- Objective: correct fail-open runner occupancy detection and enforce the
  machine schedule ceiling without interrupting active jobs.

## Implemented Locally

- Legacy and versioned `Runner.Worker` process paths are detected globally, so
  reparented workers remain visible.
- Recent pickup directories and job-hook lockfiles close the listener-to-worker
  race.
- Unreadable probes fail closed.
- Inactive services with surviving workers cannot be started again.
- `max_count` caps defaults, timed schedules, and manual targets and survives
  dashboard schedule edits.
- DeskComputer's fleet contract remains four normal runners and six maximum.
- The test process now selects an isolated, automatically cleaned configuration
  directory before backend singleton imports. Tests no longer read or write the
  live operator ledgers; dispatch-router unit tests also stub spend recording.
  This removes the Windows-side race that corrupted YAML during pre-push.
- The Docker base remains on the governed Python 3.13 digest. Remote main's
  dependency-only bump to Python 3.14 violated `requires-python <3.14` and left
  Python 3.13 cleanup paths in the image; the local correction restores the
  last qualified base instead of weakening the runtime contract.
- The previously authored `test_wsl_interlock.py` coverage was restored after
  its production module reached main without the companion test, repairing the
  repository-wide backend-module coverage invariant.

## Validation

Run tests serially to avoid adding pressure to the local runner host:

```powershell
python -m pytest tests/deploy/test_runner_scheduler.py tests/test_config_schema.py tests/test_runner_capacity_cache.py -q -n 0 --basetemp=.pytest_temp_1105
ruff check deploy/runner-scheduler.py backend/config_schema.py backend/server.py tests/deploy/test_runner_scheduler.py tests/test_config_schema.py tests/test_runner_capacity_cache.py
ruff format --check deploy/runner-scheduler.py backend/config_schema.py backend/server.py tests/deploy/test_runner_scheduler.py tests/test_config_schema.py tests/test_runner_capacity_cache.py
```

## Live Drain State

On 2026-08-24, public repository access was disabled for the
`Bandwidth-Draining` group and Desktop-5 through Desktop-8 were moved into it.
All four in-flight jobs were allowed to finish. Each runner was then verified
idle in both GitHub and the local process table before its exact service was
stopped and disabled. Desktop-1 through Desktop-4 remain active and enabled;
Desktop-5 through Desktop-8 are inactive and disabled. At the post-drain audit,
host load settled to 28% CPU with 26.3 GiB free while all four retained runners
were occupied.

The Antigravity language server was also identified as an independent memory
bottleneck at approximately 23.5 GiB. A controlled recycle reduced it to about
0.3 GiB and restored host free memory from approximately 3.5 GiB to 28.5 GiB.
The broad `Repositories.code-workspace` still opens the entire repository root
plus `../../../tmp`; a future editor-workspace change should narrow indexing or
add explicit watcher and analysis exclusions without hiding active worktrees.

## Operational Boundary

The corrected dashboard has not been deployed. After review and merge, use the
reversible dashboard rollout, configure DeskComputer with `default_count: 4`
and `max_count: 6`, and verify scheduler state against the GitHub runner view.
