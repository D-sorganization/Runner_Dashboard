# Runner Dashboard Handoff

## Current Work

- Branch: `fix/issue-1110-runtime-complete-artifact`
- Base: remote-main commit `2757f1743a06c2685cac7dd4b61d7637a9420bf6`
  from merged PR #1109.
- Objective: make the immutable artifact independently runnable and fail closed
  before a dashboard restart when its runtime is incomplete.

## Implemented Locally

- Artifact schema v2 now requires the locked dependency file, a Linux
  wheelhouse, and the root-level WSL service helper.
- Packaging selects Python `>=3.11,<3.14`, records the exact wheel ABI minor,
  and performs a clean offline install plus dependency/import smoke test.
- Installation selects that exact Python minor, installs with hashes, fails
  closed on any dependency or import error, and preserves runtime databases,
  state ledgers, histories, and `.env` across the code replacement.
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

Validated serially to avoid adding pressure to the local runner host:

- Full Python suite passed with the governed default exclusions; platform-only
  skips and one established frontend-integrity xfail remained.
- All 1,062 Vitest tests passed across 117 files.
- Ruff, Python formatting, ESLint, TypeScript, shell syntax, ShellCheck, and the
  production Vite build passed.
- The final 16 MiB v2 tarball completed a clean offline installation. A second
  installation over sentinel `.env`, SQLite, and history files preserved all
  three byte-for-byte and passed the runtime import smoke check.

## Live Drain State

On 2026-08-24, public repository access was disabled for the
`Bandwidth-Draining` group and Desktop-5 through Desktop-8 were moved into it.
All four in-flight jobs were allowed to finish. Each runner was then verified
idle in both GitHub and the local process table before its exact service was
stopped and disabled. Desktop-1 through Desktop-4 remain enabled so the
governed schedule can supply overnight capacity; Desktop-5 through Desktop-8
are inactive and disabled. At 14:58 PDT the graceful drain reached its weekday
target: Desktop-1 and Desktop-2 active and busy, Desktop-3 and Desktop-4
inactive, six total runners offline, approximately 25 GiB available memory,
and load average falling below 2. No worker process was killed.

The Antigravity language server was also identified as an independent memory
bottleneck at approximately 23.5 GiB. A controlled recycle reduced it to about
0.3 GiB and restored host free memory from approximately 3.5 GiB to 28.5 GiB.
The local `Repositories.code-workspace` no longer opens `../../../tmp` and now
excludes generated dependency, cache, build, and distribution trees from file
watching, search, and Python analysis while retaining active worktrees.

## Operational Boundary

Runner Dashboard 4.9.28 remains deployed from verified remote-main commit
`8ec28b684f20462a720e28823dc167419e9064d4`; rollback snapshots were created.
The live schedule is backed up at
`~/.config/runner-dashboard/runner-schedule.json.pre-1106-20260824` and now has
`default_count: 2`, `max_count: 4`, weekday daytime count `2`, and overnight
and weekend count `4`. The patched unit uses dashboard Python 3.12.12 and is
draining safely. PR #1109 and version 4.9.29 are merged, but the first 4.9.29
artifact rollout was rolled back because the artifact omitted its runtime
wheelhouse and root-level port helper. Issue #1110 owns the fail-closed v2
artifact repair. A complete 16 MiB Linux v2 artifact was built and passed its
isolated offline install/import smoke test locally; protected merge and an
exact-main rebuild remain required before live deployment. Do not redeploy
4.9.29, move runners out of `Bandwidth-Draining`, or restart Desktop-5 through
Desktop-8.
