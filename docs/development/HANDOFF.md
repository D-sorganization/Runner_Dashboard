# Runner Dashboard Handoff

## Current State

- Branch: `fix/1115-maintenance-drain`; local implementation commit
  `76ec074`; protected PR #1116 is open for issue #1115.
- Base: remote-main commit `30de9d024465f562e38ec98165c8086a4209a1d8`.
- PR #1114 merged as that remote-main commit and makes the governed scheduler
  the sole DeskComputer capacity-recovery authority.
- Runner Dashboard 4.9.30 is deployed from an exact-main schema-v2 artifact on
  DeskComputer and reports that commit through `/api/deployment`.
- DeskComputer is stabilized at two local runners: Desktop-1 and Desktop-2 are
  active, Desktop-3 through Desktop-8 remain disabled/inactive, and the fleet
  monitor and scheduler are inactive. The shared drain marker prevents
  automatic expansion. Post-restore host evidence showed about 37.8 GiB free
  RAM, 27% CPU, and WSL load 0.36 while one of the two runners accepted work.

## Implemented

- Both canonical Windows entry points accept one shared configurable drain
  marker and exit before WSL, scheduler, SSH, dashboard, or GitHub recovery
  side effects while it exists. `-FunctionsOnly` remains available.
- The runner-offline runbook records controlled drain and restoration under
  the two-normal/four-maximum governed schedule.

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

- Issue #1115 focused PowerShell contracts: 43 passed in isolated serial mode.
- Ruff lint and format checks passed for both changed Python test files;
  `git diff --check` passed.
- The first protected full-suite run exposed Linux `USERPROFILE` absence (7
  failures, 3,015 passes). The default now uses the cross-platform .NET user
  profile API; the 43 focused contracts passed again before the corrective
  push.
- An expanded artifact/deployment test selection stalled in established global
  test startup and was terminated without leaving a worker; it is not claimed
  as passing. Protected CI must run the repository-wide gates.

- Full Python suite passed with the governed default exclusions; platform-only
  skips and one established frontend-integrity xfail remained.
- All 1,062 Vitest tests passed across 117 files.
- Ruff, Python formatting, ESLint, TypeScript, shell syntax, ShellCheck, and the
  production Vite build passed.
- The final 16 MiB v2 tarball completed a clean offline installation. A second
  installation over sentinel `.env`, SQLite, and history files preserved all
  three byte-for-byte and passed the runtime import smoke check.
- Protected PR #1111 passed its required Python, frontend, browser, security,
  performance, and policy checks before squash auto-merge.

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

Runner Dashboard 4.9.30 is deployed from verified remote-main commit
`52635e4d3e0e5fbe71ffd10d232bbad6321fed99`. The immutable artifact is stored
at `C:\Users\diete\Artifacts\Runner_Dashboard\4.9.30\dashboard-4.9.30.tar.gz`
with SHA-256 `ded1bbfe64414d263bca338713262964145651dd244d983b8933d5de0f745933`.
The immediate rollback snapshot is
`/home/dieterolson/actions-runners/dashboard.bak.20260824_152249`.
The live schedule is backed up at
`~/.config/runner-dashboard/runner-schedule.json.pre-1106-20260824` and now has
`default_count: 2`, `max_count: 4`, weekday daytime count `2`, and overnight
and weekend count `4`. The deployed venv uses Python 3.11.14, matching the
artifact ABI. Post-deploy checks reported `ready`, correct capacity semantics,
two online/busy workers, six offline workers, 25 GiB available memory, load
average near 1, byte-identical state/history ledgers, and a healthy SQLite
database. Desktop-5 through Desktop-8 remain disabled; do not move them out of
`Bandwidth-Draining` or restart them without a separately reviewed capacity
change.
