# Keep Runtime Staff Assets Separate From Developer Checkouts

The dashboard reads role definitions, knowledge manifests, playbooks and policies
from `STAFF_RM_ROOT`. `STAFF_ROLES_DIR` must point to that checkout's `staff/roles`
directory. `STAFF_REPOS_ROOT` separately locates the repositories agents work on.

Use a dedicated clean `main` checkout for runtime assets. A developer checkout can
contain valid, unfinished work; the RM sync service deliberately refuses to
fast-forward it. This can leave new role capabilities and knowledge manifests
unavailable even after their PRs merge. Preserve that checkout and its files.

## Prepare and Validate

1. Inspect the effective service environment without printing credentials. Check
   both `~/.config/runner-dashboard/env` and any additional `EnvironmentFile`
   overrides. Record only the asset paths, deployed SHA and current role errors.
2. With the dashboard's isolated `GIT_CONFIG_GLOBAL` and existing credential
   helper, clone `D-sorganization/Repository_Management` on `main` into a dedicated
   runtime directory, such as
   `~/.local/share/runner-dashboard/repository-management`. Never overwrite an
   existing checkout. An existing managed checkout must be clean and on `main`.
3. Validate the candidate with the **deployed dashboard's** Python and role
   executor before changing its environment. Set `STAFF_RM_ROOT` and
   `STAFF_ROLES_DIR` only in the validation subprocess; load all roles with
   `staff.roles.load_roles` and require `role_validation_errors` to be empty.
   Load `staff/knowledge/cartographer.yml` with `knowledge_pack.load_manifest` and
   verify its six repositories. Also verify group/policy files the service uses.
4. A tool declared by a role must have a working executor. For example, a
   dashboard lacking the indexed `search_code` context path rejects affected
   roles. Deploy the tested capability first; do not remove validation or edit
   role YAML to hide that incompatibility. A healthy HTTP endpoint alone does
   not prove role readiness.

## Activate the Validated Assets

Confirm the service has no active staff runs, another deployment is not in
progress, and the source/asset files have not changed since validation. Preserve
a mode-0600 rollback copy of the runtime environment. Atomically replace only
`STAFF_RM_ROOT` and `STAFF_ROLES_DIR`, checking their expected previous values and
the file contents immediately before replacement. Preserve every other value
and the existing permissions. Never print or commit this file or its backup.

Keep `STAFF_RM_PYTHON` on the existing supported Python environment, and leave
`STAFF_REPOS_ROOT` unchanged. This change supplies runtime assets; it does not
move agent coding work or alter developer repositories.

For this environment-file bootstrap change, restart the existing dashboard unit
when idle, then check health, roster validity, Barb availability and the effective
asset paths. Confirm the deployed SHA again rather than assuming a restart
deployed a new artifact. Run the existing `runner-dashboard-rm-sync.service`
through its systemd user unit and inspect its result. Do not run concurrent
`staff.rm_sync` processes against the checkout.

The existing timer can now safely fast-forward this clean `main` checkout and
retain its normal backup refs and source-status receipts. Do not use the runtime
assets checkout for implementation work.

## Verify Knowledge and Rollback

After a normal pack refresh, verify all six Cartographer repositories and
canonical committed-line citations through the actual chat, search and panel
paths. Check freshness and missing-source notices. The presence of a manifest
alone does not prove that the running service loaded it or rebuilt its index.

If activation fails, restore only the two previous asset-path values, checking
that the current values still match this activation. Preserve unrelated changes
made since the backup. Restart the same unit when idle and recheck health and
roster readiness. Leave the managed clone and rollback receipt available for
diagnosis; do not reset or clean the developer checkout.
