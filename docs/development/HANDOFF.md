# Runner Dashboard Handoff

## Current State

- Branch: `fix/issue-1131-release-4933-v2`, based exactly on protected remote
  `main` at `4495163d1bd4a32c607e1ae1d7c1a58dc1a2e0f2` (the normal protected
  squash merge of PR #1136). Governing issue: #1131. The synchronized 4.9.33
  release metadata is local and unpublished; 27 bounded serial release/version
  contracts pass on this exact CVE-corrected base. A fresh protected PR must
  supersede #1134 without rewriting its history.
- PR #1116 merged as `4fc1c127`
  before its late full-suite failure surfaced. Protected corrective PR #1117
  then passed the complete suite and merged as `4b1605c7`; issue #1115 is
  closed.
- PR #1114 merged as `30de9d02` and makes the governed scheduler the sole
  DeskComputer capacity-recovery authority.
- Runner Dashboard 4.9.30 is the last deployed exact-main schema-v2 artifact on
  DeskComputer. Runner Dashboard 4.9.32 source predates the final #1126
  schedule correction. Release run `32818795234` pushed `v4.9.32` at the older
  `4eb9fac` commit, then failed publication on WSL checkout ownership; it left
  no release tarball or checksum. Do not rerun or delete that immutable tag.
- PR #1136 passed native CI Standard attempt 3 and Docker/Trivy attempt 3 at
  exact head `caf5cd82`, then protected-squash-merged as `4495163d`. The image
  build, fixed-package scan, SARIF staging, and hosted SARIF publication all
  passed; post-merge exact-main validation must complete before release.
- DeskComputer is in a full operator-authorized maintenance drain:
  `Ubuntu-22.04` is stopped, no local runner listener or worker is present, and
  Desktop-1 through Desktop-8 are offline. The shared drain marker is present,
  and `RunnerFleet-Health-Monitor` is disabled so automatic recovery cannot
  restart WSL or expand capacity.
- ControlTower retains one idle Windows/Matlab runner. Its Linux WSL pool is
  stopped and must remain stopped pending a safety copy/recovery of the
  suspect `ControlTower-SSD` VHDX and removal of the stale eight-runner startup
  override. The original and first evidence clone are sealed with matching
  SHA-256 provenance; a third E: repair derivative copied successfully and is
  being hashed before any container-level diagnosis. The weekly VHDX compaction
  task was disabled on 2026-08-24 so it cannot mutate the suspect image.
- The final dashboard snapshot before WSL quiesced reported Desktop-1 and
  Desktop-2 busy. Treat those jobs as potentially interrupted until their
  GitHub run conclusions are verified.

## Implemented

- `config/runner-schedule.json` now defaults to two weekday-day runners, four
  weekend-day runners, and four overnight runners, with `max_count: 4`.
- `tests/test_config_schema.py` enforces the exact canonical windows and counts
  so a 32-runner always-on default cannot silently return.
- `docs/deployment-model.md` now matches the two-normal/four-maximum contract.

- Root `package-lock.json` now contains the complete esbuild 0.28.2 platform
  dependency tree required by the resolved Vitest/Vite graph.
- Every frontend job uses strict `npm ci`; the former `npm ci || npm install`
  fallback can no longer hide an invalid lockfile from pull-request CI.
- A static regression contract enforces the fail-closed workflow behavior.

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
- The container keeps Python 3.13 on an immutable multi-platform digest and
  installs `libssl3t64`, `openssl`, and `openssl-provider-legacy` at Debian's
  exact, non-overridable `3.5.7-1~deb13u2` security version. Package resolution
  fails closed if that fixed version is unavailable, while the hash-locked
  Python dependencies, UID 10001 runtime, command, and `/livez` healthcheck
  remain unchanged.

## Merged #1105 Recovery

- Legacy and versioned `Runner.Worker` process paths are detected globally, so
  reparented workers remain visible.
- Recent pickup directories and job-hook lockfiles close the listener-to-worker
  race.
- Unreadable probes fail closed.
- Inactive services with surviving workers cannot be started again.
- `max_count` caps defaults, timed schedules, and manual targets and survives
  dashboard schedule edits.
- PR #1114 superseded the older four-normal/six-maximum setting: the governed
  DeskComputer contract is now two normal runners and four maximum.
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

### Issue #1138 guarded OGLaptop deployment

- Branch `fix/issue-1138-guarded-oglaptop-deploy` adds a manual-only,
  exact-runner workflow plus a one-time root bootstrap and root-owned
  transaction helper. The earlier rootless design was rejected because it
  could neither establish systemd authority nor guarantee rollback.
- The exact workflow's runner-1 `Runner.Worker` is the sole permitted busy
  worker. Both process ancestry and complete local/GitHub inventories must
  prove that exception; all other workers remain idle. Qualification is
  daytime-only so the canonical scheduler target is already four and neither
  the helper nor either observed scheduler invocation performs a runner action.
- The request surface is a closed JSON object on stdin to one no-argument sudo
  command. Exact tag/commit/hash/signature/attestation/archive/ABI checks occur
  before a verified snapshot and mutable-state manifest. Failure after the
  boundary restores the full prior deployment/config/systemd state.
- DeskComputer validation remains prohibited during the capacity drain. The
  new contract tests and shell/YAML checks must run only on approved OGLaptop
  capacity with explicit `-n 0` after protected review. No local pytest,
  pre-commit, render, build, or other CPU-heavy process was started while this
  implementation was authored.

Validated serially to avoid adding pressure to the local runner host:

- PR #1136 protected evidence: native CI Standard attempt 3 passed after the
  OGLaptop DNS correction and four-listener ceiling. Docker/Trivy attempt 3
  passed the image build, Trivy scan, SARIF upload/staging, action cleanup, and
  hosted SARIF publication at exact head `caf5cd82`. DeskComputer remained
  fully drained throughout.
- Exact-base 4.9.33 revalidation on OGLaptop: `python -m pytest -q -n 0
  tests/test_version_single_source.py tests/test_release_workflow_yaml.py
  tests/deploy/test_artifact_deployment.py` passed all 27 tests at protected
  base `4495163d`. The tested local and OGLaptop release/version diffs were
  byte-equivalent; DeskComputer ran no tests.
- Issue #1135 RED on OGLaptop: the focused deploy-hardening selection failed
  only the new OpenSSL refresh contract against protected main's older image
  digest and unpinned security package set. No Docker build or Trivy scan was
  run locally; both remain mandatory protected checks.
- Issue #1135 GREEN on OGLaptop: `python -m pytest -q -n 0
  tests/test_deploy_hardening.py` passed the complete 57-test static deployment
  contract, including immutable base identity, fixed OpenSSL package set,
  hash-locked application dependencies, non-root runtime, and `/livez`
  healthcheck. DeskComputer performed no test, build, Docker, or Trivy work.

- RED: the canonical-schedule contract failed because the repository still
  returned `default_count: 32` and `max_count: 32`.
- GREEN: `tests/test_config_schema.py` and
  `tests/deploy/test_runner_scheduler.py` pass with the 2/4 schedule.
- Full serial suite: 3,038 collected tests completed successfully on Windows;
  expected platform skips and one established frontend xfail remain.
- Repository-wide Ruff lint and Ruff format checks pass. Unscoped Black and
  Mypy checks expose pre-existing baselines (303 files and 761 errors); no
  unrelated mass formatting or typing changes were made.
- RED: `python -m pytest -q tests/test_frontend_typecheck_gate.py` failed on
  the four permissive `npm ci || npm install` workflow steps.
- GREEN: the focused frontend/release workflow contract suite passed (16
  tests), and npm 10.8.2 accepted a Linux/x64 dry-run clean install from the
  regenerated lockfile.

- Issue #1115 focused PowerShell contracts: 43 passed in isolated serial mode.
- Ruff lint and format checks passed for both changed Python test files;
  `git diff --check` passed.
- The first protected full-suite run exposed Linux `USERPROFILE` absence (7
  failures, 3,015 passes). The default now uses the cross-platform .NET user
  profile API; the 43 focused contracts passed again before the corrective
  push.
- Corrective PR #1117 passed all protected gates, including the full Python
  suite, before squash merge to remote `main` as `4b1605c7`.
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
The initial surplus-runner drain allowed all four in-flight jobs to finish,
then left only Desktop-1 and Desktop-2 active. The subsequent
operator-authorized full drain stopped the `Ubuntu-22.04` distribution and left
every Desktop runner offline. The shared drain marker and disabled
health-monitor task prevent automatic recovery. After the full drain, host
evidence showed about 40.1 GiB available memory and 14.2% CPU, with WSL
stopped. The last dashboard snapshot immediately before WSL quiesced still
reported Desktop-1 and Desktop-2 busy, so their GitHub conclusions require
verification and this handoff does not claim those workers completed normally.

The Antigravity language server was also identified as an independent memory
bottleneck at approximately 23.5 GiB. A controlled recycle reduced it to about
0.3 GiB and restored host free memory from approximately 3.5 GiB to 28.5 GiB.
The local `Repositories.code-workspace` no longer opens `../../../tmp` and now
excludes generated dependency, cache, build, and distribution trees from file
watching, search, and Python analysis while retaining active worktrees.

## Operational Boundary

Runner Dashboard 4.9.30 remains the last deployed version, from verified
remote-main commit
`52635e4d3e0e5fbe71ffd10d232bbad6321fed99`. The immutable artifact is stored
at `C:\Users\diete\Artifacts\Runner_Dashboard\4.9.30\dashboard-4.9.30.tar.gz`
with SHA-256 `ded1bbfe64414d263bca338713262964145651dd244d983b8933d5de0f745933`.
The immediate rollback snapshot is
`/home/dieterolson/actions-runners/dashboard.bak.20260824_152249`.
The stopped host's governed schedule is backed up at
`~/.config/runner-dashboard/runner-schedule.json.pre-1106-20260824` and now has
`default_count: 2`, `max_count: 4`, weekday daytime count `2`, and overnight
and weekend count `4`. The deployed venv uses Python 3.11.14, matching the
artifact ABI. Earlier post-deploy checks reported `ready`, correct capacity
semantics,
two online/busy workers, six offline workers, 25 GiB available memory, load
average near 1, byte-identical state/history ledgers, and a healthy SQLite
database. Desktop-5 through Desktop-8 remain disabled; do not move them out of
`Bandwidth-Draining` or restart them without a separately reviewed capacity
change.

## Next Steps

1. Complete exact-main post-merge validation for `4495163d`.
2. Close #1134 as superseded without rewriting its history, publish this fresh
   #1131 branch, and require an ordinary protected merge with all gates green.
3. Verify the replacement PR's signed tarball, checksum, cosign bundle, SBOM,
   provenance, schema-v2 metadata, offline wheelhouse, and exact protected
   source SHA before deployment.
4. Deploy the post-#1125 immutable artifact only after review, verify the 2/4
   schedule through a complete five-minute timer cycle, then consider restoring
   exactly two runners.
5. Keep DeskComputer fully drained. Recover ControlTower Linux capacity only
   after a verified VHDX safety copy and correction of its stale startup task
   and eight-runner override; then activate exactly two runners and observe
   pressure before allowing the four-runner ceiling.
