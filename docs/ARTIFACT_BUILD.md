# Runner Dashboard Artifact Build & Deploy

This document describes the versioned-artifact build flow introduced for issue
[#584](https://github.com/D-sorganization/Repository_Management/issues/584).
It complements the existing source-copy deploy path documented in
`docs/dashboard_deployment_guide.md` — the artifact flow is additive and does
not replace the current `setup.sh` / `update-deployed.sh` wrappers.

## What is the artifact?

A single tarball `dashboard-<version>.tar.gz` produced by the
`Build Dashboard Artifact` workflow (`.github/workflows/build-dashboard-artifact.yml`).
It contains everything a runner node needs to install the dashboard without
needing the full repository checkout:

```
dashboard-<version>.tar.gz
├── VERSION                 # semver, copied from runner-dashboard/VERSION
├── deployment.json         # machine-readable build metadata (git sha, ts, version, ...)
├── FILES.txt               # deterministic file inventory (installer validation input)
├── README.md               # copy of runner-dashboard/README.md
├── local_apps.json         # local app manifest used by the deployed dashboard
├── refresh-token.sh        # root-level service helper consumed by setup/systemd
├── backend/
│   ├── src/                # runner-dashboard/backend/*
│   └── wheels/             # vendored pip wheels of backend/requirements.txt
├── frontend/               # static assets (index.html, JSX, icon, manifest)
├── deploy/                 # setup.sh, update-deployed.sh, systemd units, helpers
└── config/                 # optional runner-dashboard/config/* (if present)
```

`deployment.json` mirrors the fields written by
`runner-dashboard/deploy/write-deployment-metadata.sh`, so the
`/api/deployment` and `/api/health` endpoints can report the same identity
regardless of whether the node was installed from artifact or from the source
tree.

The published deployment metadata now also carries a `compatibility` block with
the artifact schema, Python runtime floor, and dashboard service name so
installers can reject mismatched release bundles before overwriting a host.

## Version source of truth

`runner-dashboard/VERSION` is the semantic version for the dashboard. Bump it
on any deployment-relevant change.

- **Push to `main`** (non-tag): the workflow builds `dashboard-<VERSION>.tar.gz`
  using the `VERSION` file and uploads it as a **workflow artifact** with
  30-day retention. Useful for smoke-testing and staged rollouts.
- **Tag `dashboard-v<semver>`**: the workflow uses the tag's semver (stripping
  the `dashboard-v` prefix) and attaches the tarball + `.sha256` as assets on
  the matching **GitHub Release**. This is the canonical, immutable deliverable.
- **Manual dispatch**: optional `version_suffix` input lets you produce
  pre-release builds like `4.0.1-rc1`.

## Building locally

The workflow uses only stock tools, so you can reproduce it locally:

```bash
VERSION=$(cat runner-dashboard/VERSION | tr -d '[:space:]')
mkdir -p dist/backend dist/frontend dist/deploy dist/config
python -m pip wheel --wheel-dir dist/backend/wheels \
    -r runner-dashboard/backend/requirements.txt
cp -r runner-dashboard/backend  dist/backend/src
cp -r runner-dashboard/frontend/. dist/frontend/
cp -r runner-dashboard/deploy/.   dist/deploy/
cp    runner-dashboard/deploy/refresh-token.sh dist/refresh-token.sh
cp    runner-dashboard/local_apps.json         dist/local_apps.json
cp    runner-dashboard/VERSION    dist/VERSION
tar -czf "dashboard-${VERSION}.tar.gz" -C dist .
sha256sum "dashboard-${VERSION}.tar.gz"
```

## Installing from artifact (sketch)

The existing `deploy/setup.sh` / `deploy/update-deployed.sh` now accept
`--artifact PATH_OR_URL` and verify the release tarball checksum before
installing it. The source checkout path remains supported for machines that
still deploy from repo state.

```bash
sudo ./deploy/setup.sh --artifact /path/to/dashboard-4.0.1.tar.gz
sudo ./deploy/update-deployed.sh --artifact https://github.com/.../dashboard-4.0.1.tar.gz
```

Until the artifact is installed, operators can manually stage a release
directory:

```bash
RELEASE_DIR="$HOME/actions-runners/dashboard/releases/4.0.1"
mkdir -p "$RELEASE_DIR"
tar -xzf dashboard-4.0.1.tar.gz -C "$RELEASE_DIR"
ln -sfn "$RELEASE_DIR" "$HOME/actions-runners/dashboard/current"
sudo systemctl restart runner-dashboard.service
```

Rollback is symmetric: re-point `current` at the previous release directory
and restart the service.

## CI behaviour summary

| Trigger                       | Artifact destination              |
| ----------------------------- | --------------------------------- |
| Push to `main` (paths filter) | Workflow artifact (30d retention) |
| Tag `dashboard-v*`            | GitHub Release asset              |
| `workflow_dispatch`           | Workflow artifact (30d retention) |

The workflow smoke-tests every build by extracting the tarball, verifying the
expected layout, and parsing `deployment.json` as JSON before upload.
