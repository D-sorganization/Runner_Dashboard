# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Routed protected dashboard releases through the canonical schema-v2
  packager and made GitHub release publication repository-explicit, preserving
  the offline wheelhouse/install contract while avoiding WSL checkout
  ownership discovery failures (#1132).
- Eliminated the nested `vitest -> vite@8.x -> esbuild@^0.27||^0.28` peer
  conflict that left a clean `npm ci` install in an invalid state (`npm ls`
  reported an unmet `esbuild` peer and pulled in an unpinned `rolldown`
  bundler binary as a transitive hard dependency). Added an npm `overrides`
  entry pinning `vitest`'s internal `vite` to the project's own `vite`
  version so only one `vite`/`esbuild` pair is ever resolved, regenerated
  `package-lock.json` from that constraint, added `.npmrc`
  (`save-exact=true`) and an `engines.node` pin for reproducible resolution,
  and added `scripts/verify-lockfile.sh` (`npm run verify-lockfile`) as a
  non-workflow sanity check that a clean-state `npm ci` + `npm ls` + `npm
run build` succeed with no unmet/invalid dependency entries. See #1085.
- Bounded queue refreshes to an eight-second WAN budget with six concurrent
  repository and job-detail requests, cached per-run job counts, and explicit
  budget-exhaustion metadata instead of allowing a slow GitHub call to stall
  queue observability indefinitely.
- Updated the resolved `cryptography` dependency to 50.0.0 to remediate
  PYSEC-2026-3552 in both CI audit paths.

## [4.9.32] - 2026-08-24

### Fixed

- Regenerated the root npm lockfile with the complete cross-platform esbuild
  0.28.2 tree required by Vitest, and removed frontend CI's `npm install`
  fallback so invalid lockfiles fail before the protected release workflow.

## [4.9.31] - 2026-08-24

### Fixed

- Made the governed runner scheduler the sole DeskComputer capacity authority.
  Fleet-health recovery now follows the current scheduled target and no longer
  starts all eight configured runner services when two daytime runners are
  intentionally online (#1113).

## [4.9.30] - 2026-08-24

### Fixed

- Made dashboard artifacts runtime-complete by packaging the locked backend
  wheelhouse and root-level service helper, selecting only governed Python
  versions with an exact wheel ABI match, preserving runtime state, and failing
  closed on offline dependency or import validation.

## [4.9.29] - 2026-08-24

### Fixed

- Run scheduler status and autoscaler probes with the dashboard virtual
  environment rather than an unsupported system-Python shebang.
- Report effective scheduled default and maximum capacity separately from the
  installed and host-limit runner counts.

## [4.9.28] - 2026-08-24

### Fixed

- Run the installed runner scheduler with the deployed dashboard virtual
  environment and fail closed when that governed Python is unavailable.

## [4.9.27] - 2026-08-24

### Fixed

- Detect versioned, reparented, and pre-fork GitHub Actions workers without
  undercounting busy runners.
- Fail closed on unreadable runner-occupancy probes and prevent duplicate
  listener starts while an orphan worker remains active.
- Preserve and enforce the per-host `max_count` schedule ceiling.
- Keep the container on the qualified Python 3.13 base required by project
  metadata and native-wheel cleanup paths.
- Isolate dispatch-router tests from the live operator spend ledger.
- Restore the WSL teardown-interlock tests required by the backend-module
  coverage invariant.

## [4.9.26] - 2026-08-14

### Fixed

- The CI Standard Python scope detector now recognises `deploy/` and `tests/`
  in addition to `backend/`. Test-only and deploy-only pull requests were
  reporting `run_python_tests=false`, which skipped `quality-gate` — the only
  required status check — along with `security-scan`, `tests`, and
  `tests-required`, letting them merge with no test signal at all. The
  "Report skipped Python validation" message already claimed `tests` was
  covered; the detector never checked it.
- Realigned `pyproject.toml`, `package.json`, `package-lock.json`, and
  `frontend/src/lib/openapi.json` to the canonical `VERSION`. They had drifted
  to 4.9.24 against a declared 4.9.25 because the `tests` job that enforces the
  single-source contract was skipped on the PR that bumped `VERSION`.

## [4.9.24] - 2026-08-03

### Fixed

- Restored the canonical version metadata to the already-declared `VERSION`
  value so the release single-source contract passes again.
- Added accurate degraded queue telemetry and reversible public CI routing.

- Loopback principals can use runner service-control logging paths without
  raising `Principal.user_id` attribute errors.
- Package and OpenAPI release metadata now match the deployed `4.9.24`
  dashboard version.
- WSL keepalive parser smoke coverage now writes its probe log/state artifacts
  under pytest `tmp_path` instead of the repository root, so the test no longer
  leaves `.test-wsl-keepalive-junk/` worktree debris behind.

## [4.9.19] - 2026-06-20

### Fixed

- Aligned release metadata with the canonical `VERSION` file.

## [4.9.18] - 2026-06-17

### Fixed

- `_read_repo_version()` no longer crashes the dashboard at import when
  `REPO_ROOT/VERSION` is missing. `REPO_ROOT` is operator-overridable via
  `RUNNER_DASHBOARD_REPO_ROOT` and on some deploys points at a sibling repo
  (e.g. `Repository_Management`) that has no `VERSION` file. The reader now
  falls back to the deployed backend's own `BACKEND_DIR.parent/VERSION` and
  finally to `"0.0.0"` when neither file exists. A `VERSION` file that exists
  but is malformed still raises, so a genuinely bad version is never silently
  accepted.

## [4.9.17] - 2026-06-15

### Changed

- Moved deployment/orchestration router helper wiring to typed FastAPI
  app-state dependency objects for #949, removing module-global optional
  callables from that backend slice.

## [4.9.16] - 2026-06-14

### Fixed

- Aligned the dashboard's release metadata with the canonical `VERSION` file:
  backend runtime version, Python package metadata, frontend package metadata,
  lockfiles, and `SPEC.md` now track `4.9.16` under a regression test.

### Added

- Queue reaper unroutable-job detection (`backend/queue_cleanup.py`): a new
  `unroutable-label` stale reason flags queued runs whose `runs-on` labels are
  carried by no online runner (e.g. a removed/renamed tier such as
  `d-sorg-fleet-16core`). These previously sat queued indefinitely and were
  invisible to the reaper. Marked `safe_to_cancel` since they can never start;
  fails safe when the runner inventory or job metadata is unavailable.
- `backend/pyproject.toml` and `backend/uv.lock` for reproducible backend dependency resolution.
- Root-level `uv.lock` for reproducible project dependency resolution.
- `useTimeAgo` hook (`frontend/src/hooks/useTimeAgo.ts`) and `<TimeAgo>` primitive
  (`frontend/src/primitives/TimeAgo.tsx`) for relative timestamp rendering
  (`"just now"`, `"2m ago"`, `"3h ago"`, `"yesterday"`, absolute dates beyond
  48h). Future timestamps render `"soon"`; invalid input degrades to the raw
  value. Wired into `Reports/Mobile.tsx` Modified field as first call-site.
  Closes #725.

### Changed

- Extracted security utilities from `backend/server.py` into `backend/security.py` to begin god-module refactoring.

### Fixed

- Queue diagnosis now treats `d-sorg-fleet*` labels as self-hosted fleet work
  even when GitHub's queued-job metadata omits the literal `self-hosted` label,
  so `/api/queue/diagnose` no longer reports local fleet jobs as
  GitHub-hosted waits.
- Overview summary (`/api/stats`) showed false zeros for open PRs, queue depth,
  and machines under partial GitHub failure. The PR/issue search, queue
  fan-out, and fleet probe shared one timeout budget, so a slow search or
  secondary rate-limit zeroed them all at once (while the toolstrip's
  `/api/queue` stayed correct). Now each source is budgeted independently, the
  queue is reused from its own resilient cache, and failed fields fall back to a
  `stats:stale` last-known-good snapshot instead of zero (`backend/routers/repos_stats.py`).
- Per-runner Python tool-cache isolation (`deploy/migrate-runner-units.sh`):
  every `actions.runner.*.service` drop-in now exports a private
  `RUNNER_TOOL_CACHE` (default `<WorkingDirectory>/_work/_tool`, overridable via
  `RUNNER_TOOL_CACHE_ROOT`). This eliminates the shared-`.shared-tool-cache`
  race where concurrent jobs on one host corrupted `actions/setup-python`
  ("Directory not empty", exit-127 "python: command not found",
  "ModuleNotFoundError: No module named 'http'"). `deploy/runner-cleanup.sh`
  already GCs `_work/_tool`, so the private caches stay bounded.
- `deploy/runner-corruption-scan.sh` now emits a third Prometheus signal,
  `kind="python_toolcache"`, counting incomplete Python tool-cache trees
  (a `<version>/<arch>` dir missing the toolkit's `.complete` marker) so the
  fleet can watch the corruption trend toward zero after rollout.
- Corrected broken issue reference `#944` to `#161` in `pyproject.toml` and CI workflow.
- Restored missing `PROVIDERS_WITH_MODEL` definition in frontend bundle.
- Removed stale `agent_remediation_140.py` from repo root and added `/*_[0-9]*.py` to `.gitignore`.

## [4.0.1] - 2026-04-26

### Fixed

- CSP: kept `strict-dynamic` in the `script-src` directive (it remains
  required for compatibility with the CDN-loaded React bootstrap and is
  still present in `backend/middleware.py`); restored `'unsafe-inline'`
  on `style-src` to fix the blank-dashboard regression (#172).

  Note: an earlier draft of this changelog entry stated that
  `strict-dynamic` had been removed. That was inaccurate — the directive
  was retained. This entry has been corrected (issue #394).

## [4.0.0] - 2026-04-25

### Added

- Queue stale-queue detection, bulk purge, and scheduled auto-cleanup.
- Principal Management UI scaffolding.
- Identity impersonation flow and `SPEC.md` updates (Epic #63 Wave 5).
- Fair Sharing UI (Wave 3).

### Fixed

- Prevented pytest failures from being silently masked in CI (#148).

## [3.0.0] - earlier

### Added

- Initial Runner Dashboard with FastAPI backend and React SPA frontend.
- GitHub OAuth login flow and service-token support.
- Fleet-wide runner coordination and job queue management.
- CSP and security headers.
