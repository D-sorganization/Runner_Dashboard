# Clean-Host Smoke Test: Install, Upgrade, Rollback (#3726)

A manual runbook for verifying install, upgrade, and rollback on a freshly
provisioned host, using no pay-per-use services. This is deliberately a
checklist a human runs by hand, not a CI job: **HOLD #2657** blocks any
Runner Dashboard deploy or release automation, so no workflow in
`.github/workflows/` is allowed to drive a real `deploy/setup.sh` install
cycle. There is no automated clean-host smoke test today; this doc exists so
a person can run one consistently. If #2657 ever lifts, this checklist is the
starting point for a scripted version — don't skip straight to automating it.

See [`install-paths.md`](install-paths.md) for the two supported install
paths (systemd bare-metal, Docker Compose) and
[`deployment-model.md`](deployment-model.md) for the full operational
lifecycle these commands are drawn from.

## Prerequisites

- A disposable host (VM or container) with no prior Runner Dashboard
  install — a true "clean host", not a machine already running the fleet.
- No GitHub App token or other paid API key required for the steps below;
  `deploy/setup.sh` and `deploy/deploy-check.sh` work with a local-only
  configuration for this smoke test.

## Install

1. Clone the repo and run the bare-metal installer for a single-node,
   non-hub setup:
   ```bash
   sed -i 's/\r$//' deploy/setup.sh && chmod +x deploy/setup.sh
   ./deploy/setup.sh --runners 1 --machine-name smoke-test-host
   ```
2. Validate the install actually works end-to-end (not just "service is
   active" — see the header comment in `deploy/deploy-check.sh` for why that
   distinction matters):
   ```bash
   bash deploy/deploy-check.sh
   ```
   Expect exit code 0 and `ok=true` on `/api/diagnostics`.

## Upgrade

1. Make a trivial, reversible change on a branch (e.g. bump a comment) and
   land it as the new "upgrade" target.
2. Run the routine update path documented in `deployment-model.md`'s
   "Routine Update" section:
   ```bash
   deploy/update-deployed.sh
   ```
   This backs up the current install under a timestamped directory with a
   `.manifest.sha256` integrity manifest (see
   [`upgrade-rollback.md`](upgrade-rollback.md)) before applying the update.
3. Re-run `deploy/deploy-check.sh` to confirm the upgraded install is still
   healthy.

## Rollback

1. Force a rollback to the pre-upgrade backup:
   ```bash
   deploy/rollback.sh
   ```
   `rollback.sh` verifies the backup's `.manifest.sha256` before restoring
   and aborts fail-closed on a verified mismatch (see
   `upgrade-rollback.md` for the one exception: backups made before manifest
   tracking existed only warn and proceed).
2. Re-run `deploy/deploy-check.sh` one more time and confirm the dashboard is
   back to the pre-upgrade behavior.

## Recording the result

Note the host image/OS, the commit SHA tested, and the three `deploy-check.sh`
outputs (post-install, post-upgrade, post-rollback) wherever this smoke test
run is tracked (e.g. a comment on the tracking issue). This doc intentionally
does not prescribe where that record lives — Sidekick packaging and any
broader release-tracking tooling remain on hold (docs/triage only) per the
deploy epic (#3727) this issue belongs to.
