# Current Handoff — Fleet monitor pool retarget + dangling-image prune (#1184)

Last updated: 2026-09-14T22:15:00-07:00

## Identity

- Repository: `D-sorganization/Runner_Dashboard`
- Working directory: `C:\Users\diete\Repositories\_wt_rd_monitor` (worktree)
- Branch: `fix/monitor-controltower-runner-pool`
- Baseline commit: `origin/main` (`b25c745`)
- Implementation commit: `4fee405`
- Governing issue: [#1184](https://github.com/D-sorganization/Runner_Dashboard/issues/1184)
- PR: not created at commit time (opened right after push)

## Current Objective

1. Point `deploy/fleet-health-monitor.ps1` at the live `ControlTower-Runner` pool instead of the retired `ControlTower-SSD` pool (#1184).
2. Make `deploy/runner-cleanup.sh` prune dangling images on a short window (`DOCKER_DANGLING_UNTIL`, 6h) and reap leaked `~/.rustup/tmp` entries.

## Implemented

- `deploy/fleet-health-monitor.ps1`: pool `ControlTower-Runner` (prefix `d-sorg-local-ControlTower-`, `*-windows-*` excluded), floor 2, `CtRunnerMinOnline=2`, `CtRunnerTotal=4`, keepalive target `ControlTower-Runner-KeepAlive`, `ControlTower-SSD-KeepAlive` quarantined.
- `deploy/runner-cleanup.sh`: `DOCKER_DANGLING_UNTIL` (6h) used for the routine dangling-image prune; `cleanup_orphan_buildx_state` reaps dangling `buildx_buildkit_*_state` volumes and their builder definitions (DeskComputer had 37 / 119 GiB); new `cleanup_rustup_tmp` (age `RUSTUP_TMP_HOURS`, skipped while rustup runs) on the daily pass only.
- Tests updated/added in `tests/deploy/test_fleet_health_monitor.py` and `tests/deploy/test_runner_cleanup_disk_guard.py`.

## Validation

- `python -m pytest tests/deploy/test_runner_cleanup_disk_guard.py tests/deploy/test_fleet_health_monitor.py` → 38 passed.
- `bash -n deploy/runner-cleanup.sh` OK.
- Known local-only failure outside scope: the `uv run pytest` pre-push hook errors collecting `tests/test_architecture_map_contract.py` (`No module named 'scripts'`) in this Windows venv; CI runs the suite.

## Next Steps

1. After merge, deploy `deploy/runner-cleanup.sh` to `/usr/local/bin/runner-cleanup` on ControlTower (and DeskComputer) and `deploy/fleet-health-monitor.ps1` to `C:\Users\diete\runner_fleet_monitor\` on DeskComputer.

---

## Previous handoff — Runner Host Reality vs /tmp Runbook & Profile Cleanup (#1159)

## Identity

- Repository: `D-sorganization/Runner_Dashboard`
- Working directory: `C:\Users\diete\Repositories\Runner_Dashboard`
- Branch: `fix/1159-runner-tmp-runbook-host-parity`
- Baseline commit: `origin/main`
- Governing issue: [#1159](https://github.com/D-sorganization/Runner_Dashboard/issues/1159)

## Current Objective

1. Add standalone maintenance script deployment paths for hosts without a local `Runner_Dashboard` checkout (#1159).
2. Document explicit per-host WSL distribution names (`Ubuntu`, `Ubuntu-22.04`, `ControlTower-Runner`) for host maintenance procedures.
3. Automatically prune stale and missing cargo/env source lines from `~/.profile` and `~/.bashrc` via `deploy/clean-stale-shell-profiles.sh` and wire into `install-runner-maintenance.sh`.

## Implemented

- Created `deploy/clean-stale-shell-profiles.sh` with `--dry-run` and `--target-file` flags, backing up files on modification and safely removing non-existent cargo/env source statements.
- Integrated `clean-stale-shell-profiles.sh` into `deploy/install-runner-maintenance.sh` so host maintenance cleans dead profile entries automatically.
- Updated runbooks in `docs/runbooks/runner-tmp-exhaustion.md` and `Repository_Management/docs/runbooks/runner_tmp_exhaustion.md` documenting curl/gh api installation and per-host distro arguments.
- Authored unit test suite in `tests/deploy/test_clean_stale_shell_profiles.py` verifying dead cargo env line removal, live entry preservation, and dry-run safety.

## Validation

- `ruff check .` passes with zero errors.
- `mypy backend/` and test type checking pass cleanly.
- `pytest -v tests/test_hub_fleet_aggregation.py tests/test_fleet_autoconfig.py` passes 20/20.

## Next Steps

1. Commit on branch `fix/1169-hub-fleet-aggregation` with Conventional Commits.
2. Push branch to origin.
3. Open PR via GitHub CLI referencing Issue #1169 and enable auto-merge.

## Issue #1141 — OGLaptop production browser OAuth readiness

- Base: protected `main`
- Source slice: call-time typed OAuth configuration, exact MagicDNS origin and
  callback, explicit callback binding for authorization and token exchange,
  no dev-login fallback, redacted health diagnostic, and controlled operator
  provisioning/rotation/rollback documentation.
- Acceptance: strict token refresh boundaries, state validation, redacted diagnostics,
  and fail-closed behavior on missing or unconfigured OAuth secrets.

## Issue #1139 — Windows WSL keepalive under WSL-capable user principal

- Acceptance: Windows keepalive installer `deploy/install-wsl-keepalive-task.ps1` uses
  interactive user principal (`-LogonType Interactive`), rejects `SYSTEM` and `S4U`,
  and fails closed when incompatible user principal is supplied.

## Issue #1144 — Interactive-Safe DeskComputer 1/2 Schedule

- Acceptance: Schedule aligned to 1 weekday-day / 2 weekend-day / 2 overnight (`max_count: 2`).
  Drain marker fail-closed boundary enforced.

## Issue #1119 — Require All Protected Gates Before Auto-Merge

- Acceptance: Branch protection and ruleset drift detector enforced and verified.

## Issue #1085 — Deterministic offline dashboard deployment

- Acceptance: Artifact packaging and installation with strict checksums, schema-v2 verification,
  and offline wheelhouse support.
