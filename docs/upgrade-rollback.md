# Upgrade, Migration, and Rollback Guarantees

Part of the deployment and release hardening epic (#3727). This page answers
the question an operator asks before running an upgrade: **if this breaks,
what does the deploy tooling actually guarantee about getting back to a
known-good state?**

Deploy and release pipeline changes for Runner_Dashboard are on hold
(HOLD #2657). This page only documents the real behavior of the existing
`deploy/` scripts; it changes none of them.

## Backup integrity manifest (what exists today)

Every upgrade through `deploy/update-deployed.sh` first calls `backup_dir()`
(`deploy/lib.sh`) on the current deploy directory. That helper:

- Copies the directory to a timestamped `<dir>.bak.<timestamp>` snapshot.
- Writes a SHA-256 manifest, `<backup>.manifest.sha256`, covering every file
  in the snapshot — this is the "backup integrity manifest."

`deploy/rollback.sh --list` reads that manifest to report each backup's
integrity status (`ok`, `missing`, or `FAIL`) and the deployed `VERSION`
alongside it, so an operator can see which backups are verifiably intact
before picking one.

## Rollback is fail-closed on a verified integrity failure — not on a missing manifest

`deploy/rollback.sh` is the manifest-aware rollback path:

- If the manifest exists and `sha256sum -c` fails, the script **aborts
  the rollback** ("Backup integrity check FAILED ... Aborting rollback") —
  fail-closed, so a corrupt backup is never applied.
- If the manifest is simply absent (a backup made before integrity tracking
  existed), the script **warns and proceeds without verification** rather
  than refusing to roll back. Pre-manifest backups are not covered by the
  fail-closed guarantee above; only backups made since `backup_dir()` added
  manifest writing are.
- Before applying the backup, it snapshots `~/.config/runner-dashboard` and
  the deploy directory's `config/` into a `.state.tar.gz` alongside the
  backup, so rollback does not silently discard operator config changes
  made after the backup was taken.
- DB schema state is rolled back with `alembic downgrade -1` against the
  deployed backend, unless `--skip-db` is passed. If the downgrade command
  fails, the script aborts ("DB migration rollback failed. Aborting to
  avoid schema/code mismatch") rather than leaving code and schema out of
  sync.

## Gap tracked by this issue: the update-failure auto-rollback path skips manifest verification (#3725)

`update-deployed.sh` creates its pre-upgrade backup with the manifest above,
but if the subsequent deploy step fails, it calls `deploy/rollback-deployed.sh`
to recover — not `deploy/rollback.sh`. `rollback-deployed.sh` (issue #713)
restores via a plain `rsync` from the backup directory and never reads or
checks a `.manifest.sha256` file.

In short: a backup taken by `update-deployed.sh` is integrity-manifested, but
only the *manual* rollback path (`deploy/rollback.sh`) verifies that manifest
before restoring. The *automatic* rollback-on-failure path
(`rollback-deployed.sh`) restores unconditionally, with no integrity check and
no DB migration downgrade. An operator recovering from a failed automatic
rollback should also run `deploy/rollback.sh --list` to confirm the resulting
state's backup integrity rather than assuming the auto-rollback verified it.
Closing that gap is deploy-pipeline work and is not implemented by this page
(HOLD #2657).

See also: [`docs/releases.md`](releases.md) for the signed-artifact release
flow, and [`docs/install-paths.md`](install-paths.md) for the two supported
install paths.
