# Runner Dashboard Handoff

## Current Work

- Issue: [#1107](https://github.com/D-sorganization/Runner_Dashboard/issues/1107)
- Branch: `fix/issue-1107-scheduler-python-runtime`
- Base: remote-main commit `0b8462a3011d99f8af10ec52ef80c2af4da8c12b`
  from merged PR #1106.
- Objective: ensure the installed scheduler uses the governed dashboard Python
  runtime rather than Ubuntu 22.04's unsupported system Python 3.10.

## Implemented Locally

- `install-runner-maintenance.sh` now writes the scheduler systemd unit with
  the deployed dashboard virtual-environment interpreter and fails closed when
  that executable is unavailable.
- A static installer regression test was RED before the change and is GREEN;
  all nine disk-guard/maintenance installer tests pass serially.

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

Runner Dashboard 4.9.27 is deployed from verified remote-main commit
`0b8462a3011d99f8af10ec52ef80c2af4da8c12b`; rollback snapshots were created.
The live schedule is backed up at
`~/.config/runner-dashboard/runner-schedule.json.pre-1106-20260824` and now has
`default_count: 4`, `max_count: 6`, and four runners in each timed entry. The
patched unit uses dashboard Python 3.12.12. Its first successful application
reported `desired=4`, `online=4`, `busy=4`, `offline=4`, and `actions=0`, with a
five-minute timer scheduled. The installer-source correction is not yet merged;
push PR #1107 only after the full pre-push gate succeeds. Do not move runners
out of `Bandwidth-Draining` or restart Desktop-5 through Desktop-8 as part of
that PR.
