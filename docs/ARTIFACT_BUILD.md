# Runner Dashboard Artifact Build & Deploy

This document describes the versioned-artifact build flow introduced for issue
[#584](https://github.com/D-sorganization/Repository_Management/issues/584).
It complements the existing source-copy deploy path documented in
`docs/dashboard_deployment_guide.md` — the artifact flow is additive and does
not replace the current `setup.sh` / `update-deployed.sh` wrappers.

## What Is the Artifact?

A single tarball `dashboard-<version>.tar.gz` produced by the protected
`Release` workflow through `deploy/package-dashboard-artifact.sh`.
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
├── wsl-mirrored-port-helper.sh # root-level systemd pre/post-start helper
├── requirements.lock.txt   # hash-locked Python runtime dependency contract
├── backend/
│   ├── *.py                # runner-dashboard/backend/*
│   └── wheels/             # vendored wheels for requirements.lock.txt
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
the artifact schema, Python runtime range and exact wheel ABI minor, and
dashboard service name so installers can reject mismatched release bundles
before overwriting a host. Artifact updates preserve runtime databases, state
ledgers, histories, and `.env`; those mutable files are not release contents.

## Version Source of Truth

`runner-dashboard/VERSION` is the semantic version for the dashboard. Bump it
on any deployment-relevant change.

- A protected `main` push that changes `VERSION` builds, signs, attests, tags,
  and publishes the canonical artifact and checksum.
- Tag and manual-dispatch releases require the requested version to match
  `VERSION`; manual dispatch also supports a non-publishing dry run.
- Every published artifact records the exact workflow source SHA and retains
  the checksum, cosign bundle, SBOM, and build provenance together.

## Building Locally

Build on the Linux platform used by the runner host so native wheels match the
deployment target:

```bash
VERSION=$(grep -vE '^\s*(#|$)' VERSION | head -n1 | tr -d '[:space:]')
SHA=$(git rev-parse HEAD)
bash deploy/package-dashboard-artifact.sh \
  --output-dir /path/to/artifacts \
  --version "$VERSION" \
  --sha "$SHA"
```

## Installing From Artifact (Sketch)

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

The package command performs an isolated offline install from the completed
tarball and imports the service's core runtime modules before it reports
success. The installer rejects old schemas, missing wheelhouses or helpers,
unsupported Python versions, hash mismatches, dependency conflicts, and import
failures before a service restart is attempted.
