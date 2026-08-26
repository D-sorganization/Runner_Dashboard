# Qualified OGLaptop Release Deployment

This runbook governs the unattended production deployment path for the
OGLaptop Runner Dashboard. It deploys one exact published release through a
manual GitHub Actions dispatch, a pre-installed root-owned transaction helper,
and a redacted evidence artifact. It does not accept a source branch, checkout,
URL, artifact path, setup script, or arbitrary command.

## Safety model

The workflow is pinned to `d-sorg-local-Oglaptop-1` and serialized across the
repository. Runner 1 necessarily has a `Runner.Worker` while it executes the
deployment. That worker is the sole exception to the idle-host precondition:

1. The workflow proves its `Runner.Worker` PID is in its own process ancestry.
2. The root helper independently proves that PID belongs to `runner-1` and is
   an ancestor of the privileged process.
3. A complete, paginated GitHub inventory proves runner 1 is the exact busy
   runner, runners 2-8 are idle, runners 1-4 are online, and runners 5-8 are
   offline.
4. The artifact's scheduler independently proves the local inventory contains
   exactly runners 1-8, only runner 1 is busy, and only runners 1-4 are active.

Qualification is limited to the `weekday-day` or `weekend-day` schedule window
(07:00-22:00 America/Los_Angeles). This makes desired capacity four before,
during, and after deployment. The observed scheduler cycle must record no
runner actions. The transaction never calls `setup.sh`, the broad maintenance
installer, runner-unit migration, or direct runner service control. A deploy
outside this steady state fails before mutation.

## One-time bootstrap

Bootstrap is an explicit root operation after the issue #1138 PR reaches
protected `main`. Review the exact checkout and install a separately reviewed
cosign binary first. If `/usr/local/bin/cosign` is already root-owned and not
group/other writable:

```bash
sudo bash deploy/bootstrap-qualified-release-deploy.sh \
  --expected-commit <reviewed-protected-main-sha>
```

To install a reviewed cosign binary as part of bootstrap, provide both its local
path and independently obtained SHA-256:

```bash
sudo bash deploy/bootstrap-qualified-release-deploy.sh \
  --expected-commit <reviewed-protected-main-sha> \
  --cosign-source /path/to/cosign-linux-amd64 \
  --cosign-sha256 <64-lowercase-hex>
```

Bootstrap fails unless the checkout is clean, its exact commit matches the
operator-supplied protected-main SHA, and all three installed sources are
tracked by that commit. It makes no service changes. It installs:

- `/usr/local/sbin/runner-dashboard-qualified-deploy` (root:root, 0755);
- `/usr/local/lib/runner-dashboard/qualified-release-lib.sh` (root:root, 0644);
- the canonical OGLaptop schedule under `/usr/local/share`;
- a root-only transaction/rollback store and runner-owned 0700 inbox; and
- one sudoers command with no wildcard arguments.

The helper accepts its closed-schema request on stdin. Sudoers does not grant a
shell, systemctl, install, file-copy, command argument, or script-path surface.
Re-run bootstrap only to install a reviewed protected-main helper revision.

## Dispatch qualification

Collect these values from the published release record before dispatch:

- `target`: `OGLaptop` (the only choice);
- `version`: exact semver without `v`;
- `tag`: exact annotated `vX.Y.Z` tag;
- `release_commit`: exact 40-character commit SHA;
- `artifact_sha256`: exact lowercase artifact hash; and
- `confirm`: `DEPLOY OGLAPTOP`.

The workflow fails closed unless the tag is annotated and peels to the supplied
commit, the commit is on protected-main ancestry, and the release is published,
non-draft, and non-prerelease. It then verifies the published sidecar, cosign
bundle identity, GitHub build attestation for `release.yml` on `main`, archive
path safety, schema v2 metadata, exact commit/version, and Python 3.12 wheel ABI.
The root helper repeats the checksum, cosign, archive, metadata, and ABI checks
against copies in a root-only transaction directory.

Do not dispatch while another OGLaptop job is running, outside the daytime
window, while a runner inventory is incomplete, or before bootstrap succeeds.
Never weaken the inventory, signature, attestation, ancestry, or worker checks
to force a deployment.

## Transaction and rollback

Before the first host mutation, the helper creates one unique transaction
directory and a verified rollback snapshot containing:

- the complete deployed dashboard tree;
- dashboard configuration and local-share trees;
- the capacity drop-in, scheduler service/timer, and scheduler executable (or
  explicit absence markers);
- dashboard, scheduler, and autoscaler active/enabled state; and
- a root-only manifest plus byte copies of runtime databases, SQLite sidecars,
  histories, state ledgers, JSONL audit data, and secret/config files. The
  canonical schedule is excluded because it is replaced by qualified policy.

The signed artifact installs offline. Mutable files are restored byte-for-byte
and verified before the dashboard restarts. The helper then installs the exact
schedule, a reversible `NUM_RUNNERS=4` / `MAX_RUNNERS=8` systemd drop-in, the
artifact scheduler, and a scheduler unit whose interpreter is the deployed
`.venv`. It disables the competing autoscaler and verifies the effective
systemd authority without printing environment-file contents.

Any failure after mutation triggers automatic restoration of the dashboard,
configuration, local share, systemd files, scheduler binary, prior unit states,
and dashboard service. The root-only JSONL journal records step names and
outcomes, never commands, environment values, credentials, API responses, or
remote endpoints. Do not delete transaction directories until the release is
accepted and rollback retention is recorded.

## Acceptance evidence

The helper verifies the deployed metadata and live `/health`, `/livez`, and
`/api/version` identity. It manually invokes the scheduler once, enables the
five-minute timer, and waits up to 330 seconds for a distinct systemd
`InvocationID`. Both invocations must report desired capacity four, active
runners 1-4, only the workflow runner busy, and an empty action list. The
workflow then repeats the complete GitHub inventory proof.

An `always()` step uploads only redacted JSON evidence containing release
identity, transaction status, before/after capacity numbers, mutable-manifest
digests, scheduler-cycle duration, health booleans, and rollback result. Raw API
payloads, download/attestation logs, environment files, journal data, tokens,
and endpoint URLs are never uploaded. Preserve the workflow run ID, artifact
digest, and protected-main SHA in the repository handoff after acceptance.

## Failure handling

- **Rejected before mutation:** correct the release input, inventory, time
  window, bootstrap, or supply-chain evidence; no rollback is required.
- **Rolled back:** inspect the root-only transaction journal locally and retain
  the redacted workflow artifact. Do not expose secret-bearing snapshots.
- **Rollback failed:** stop further deployments, keep the transaction directory
  unchanged, and use the recorded snapshot under direct operator control.
- **Evidence upload failed:** treat the deployment as unqualified even if the
  service is healthy. Preserve the root evidence and rerun only after review.
- **Scheduler cycle timed out or recorded an action:** the helper rolls back.
  Diagnose scheduler/timer authority; do not manually start or stop runners as
  part of this workflow.

The generic source deployment and rollback scripts remain available for direct
operator recovery. They are not substitutes for this qualified release path.
