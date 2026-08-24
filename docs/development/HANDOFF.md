# Runner Dashboard Handoff

## Current Work

- Branch: `fix/scheduler-status-runtime`
- Base: remote-main commit `8ec28b684f20462a720e28823dc167419e9064d4`
  from merged PR #1108.
- Objective: ensure every scheduler probe uses the governed dashboard Python
  runtime and make the dashboard's capacity-count meanings unambiguous.

## Implemented Locally

- Scheduler status and autoscaler desired-capacity probes now invoke the
  scheduler through `sys.executable`; they can no longer fall back through the
  script shebang to Ubuntu 22.04's unsupported Python 3.10.
- Capacity responses use the validated schedule for `configured_runners`,
  `default_runners`, and `max_runners`; `host_runner_limit` and
  `installed_runners` preserve the separate physical/configuration context.
- The project contract remains Python `>=3.11,<3.14`; Python 3.10 is explicitly
  unsupported rather than maintained as a second runtime.

## Merged #1105 Recovery

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
python -m pytest tests/deploy/test_runner_cleanup_disk_guard.py -q -n 0 --basetemp=.pytest_temp_scheduler_python
ruff check tests/deploy/test_runner_cleanup_disk_guard.py
ruff format --check tests/deploy/test_runner_cleanup_disk_guard.py
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
The local `Repositories.code-workspace` no longer opens `../../../tmp` and now
excludes generated dependency, cache, build, and distribution trees from file
watching, search, and Python analysis while retaining active worktrees.

## Operational Boundary

Runner Dashboard 4.9.28 is deployed from verified remote-main commit
`8ec28b684f20462a720e28823dc167419e9064d4`; rollback snapshots were created.
The live schedule is backed up at
`~/.config/runner-dashboard/runner-schedule.json.pre-1106-20260824` and now has
`default_count: 2`, `max_count: 4`, weekday daytime count `2`, and overnight
and weekend count `4`. The patched unit uses dashboard Python 3.12.12 and is
draining safely. The 4.9.29 source correction is local only: do not deploy it,
move runners out of `Bandwidth-Draining`, or restart Desktop-5 through
Desktop-8 without a separately reviewed operational change.
