# Current Handoff — Release 4.9.34 Qualification, Release Recovery & Guarded OGLaptop Deployment

Last updated: 2026-08-27T06:00:00-07:00

## Identity

- Repository: `D-sorganization/Runner_Dashboard`
- Working directory:
  `C:\Users\diete\Repositories\_worktrees\runner-dashboard-issue-1138`
- Branch: `fix/issue-1138-guarded-oglaptop-deploy`
- Baseline commit: `60ecfff`
- Implementation commit: `SELF` — resolve with `git rev-parse HEAD`
- Pull request: [#1140](https://github.com/D-sorganization/Runner_Dashboard/pull/1140)
- Governing issue: [#1138](https://github.com/D-sorganization/Runner_Dashboard/issues/1138)

## Current Objective

1. Guard exact OGLaptop release deployment transactions with a workflow-dispatch-only path crossing into a root-owned no-argument helper boundary (#1138).
2. Maintain interactive-safe DeskComputer capacity policies and qualified 4.9.34 release state.

## Implemented

- Guarded OGLaptop release deployment workflow (`.github/workflows/deploy-qualified-release.yml`), helper script (`deploy/bootstrap-qualified-release-deploy.sh`), and runtime libraries.
- Strict request validation, single-busy-worker verification, signed release archive and provenance validation before host mutation.
- Rollback snapshotting, transaction journaling, and automatic rollback on post-boundary failures.
- Release recovery path for late-stage publication failures (#1129).
- Canonical schedule defaults and configuration schemas.

## Validation

- 16 guarded deployment workflow contract tests passed in `tests/test_qualified_release_deploy_workflow.py`.
- Release recovery and workflow hygiene tests pass serially.
- Scoped pre-commit passes Ruff lint/format, Prettier, gitleaks, and detect-secrets.
- `git diff --check` passes.

## Next Steps

1. Merge PR for feat(deploy) via squash auto-merge after CI passes.
2. Verify production OAuth readiness on OGLaptop per `docs/runbooks/oglaptop-production-oauth.md`.
3. Verify WSL resident keepalive task runs under the signed-in interactive user principal without SYSTEM/S4U failure modes.
4. Verify DeskComputer interactive-safe 1/2 capacity policy and drain marker boundary before re-entry.

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
