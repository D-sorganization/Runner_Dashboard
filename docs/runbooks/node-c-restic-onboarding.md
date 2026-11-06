# node-c restic onboarding and pre-backup cleanup (#4010)

Panel verdict `th_958c7b53ae45` (approved 2026-11-04). **node-c** is the Linux fleet
node (map from `machine_registry.yml` on the hub). This runbook covers **client
onboarding** and a **report-only cleanup audit** ahead of the first backup. It does
**not** perform rushed deletions on live hosts.

## Principles

1. **Audit first** — Run `deploy/fleet-backup/node-c-cleanup-audit.sh` and review output. Reclaim space with the existing fleet GC (`deploy/runner-cleanup.sh`, `deploy/fleet_storage_gc.py`) only after operator review.
2. **No rushed live deletions** — The audit script refuses `--delete`, `--apply`, and `NODE_C_CLEANUP_ALLOW_DELETE=1`. Scheduled backups refuse `RESTIC_DRY_RUN=1`.
3. **Hub dependency** — **hub-a** must run the append-only rest-server from `docs/runbooks/fleet-restic-backup.md` with a private-repo user matching **node-c** (see `deploy/fleet-backup/clients.example.yml` when present on main).

## Pre-backup cleanup audit (report-only)

On node-c:

```bash
cd ~/actions-runners/dashboard
bash deploy/fleet-backup/node-c-cleanup-audit.sh
# optional machine-readable lines:
bash deploy/fleet-backup/node-c-cleanup-audit.sh --json
```

Nothing is removed. Use the report to decide whether to run routine GC elsewhere; do not bypass the audit with deletion flags (they are rejected by design).

## Onboarding restic (dry-run default)

1. Install `restic` on node-c.
2. Create `/etc/node-c-restic/restic-password.txt` (mode `0600`) with a unique repo password (not shared across fleet clients).
3. Copy `deploy/fleet-backup/node-c-client.env.example` to `/etc/node-c-restic/env` (mode `0600`). Set `RESTIC_REPOSITORY` to the hub-a Tailscale URL:

   ```text
   rest:http://hub-a.fleet-example.ts.net:8000/node-c
   ```

4. Preview onboarding (default `RESTIC_DRY_RUN=1` in the example env):

   ```bash
   sudo NODE_C_RESTIC_ENV=/etc/node-c-restic/env bash deploy/fleet-backup/node-c-onboard-restic.sh
   ```

5. When preview looks correct, set `RESTIC_DRY_RUN=0` in `/etc/node-c-restic/env` and re-run onboarding to `restic init` (if needed) and the first backup.

## Scheduled backups

Install systemd units from `deploy/fleet-backup/systemd/` (substitute `@ENV_FILE@` and `@DEPLOY_DIR@` the same way as other fleet backup units). Enable the timer only after a successful manual backup with `RESTIC_DRY_RUN=0`.

## Related issues

- `#4016` — hub-a rest-server deployment
- `#4012` — broader fleet rollout after WSL export pilot (#4011)
- `#4010` — this node-c track (audit + onboarding)
