# Fleet restic backup hub (DeskComputer)

Panel verdict `th_958c7b53ae45` (approved 2026-11-04). This runbook covers **PR 1 of 3**: the append-only [restic rest-server](https://github.com/restic/rest-server) on DeskComputer. Client backup jobs (`#4011`, `#4012`) are separate issues.

## Architecture

- **Hub:** DeskComputer runs `rest-server` with `--append-only` and `--private-repos`.
- **Transport:** Clients reach the hub only on the DeskComputer **Tailscale IPv4** (no public cloud backends, no LAN bind).
- **Identity:** One htpasswd user per fleet client; username equals the private repo name under the data directory.
- **Secrets:** Passwords and htpasswd live on hosts only (`/etc/restic-rest-server/htpasswd` on the hub; client-side `RESTIC_PASSWORD` in a local file). Nothing in git.

Reference layout: `deploy/fleet-backup/clients.example.yml`.

## Prerequisites (operator, before go-live)

1. **Tailscale ACL** — Apply `docs/tailscale/acl-fleet-backup.hujson` in the admin console: drop the legacy `tcp:445` backup grant and allow `tcp:8000` from member/hub tags to the DeskComputer backup tag. **The server must not be relied on until this is live.**
2. **Tag DeskComputer** with the backup hub tag used in the ACL (example: `tag:fleet-backup-hub`).
3. **htpasswd on DeskComputer** — One bcrypt user per client (usernames from `clients.example.yml`):

   ```bash
   sudo install -d -m 0750 /etc/restic-rest-server
   sudo htpasswd -B -c /etc/restic-rest-server/htpasswd oglaptop   # first user only
   sudo htpasswd -B    /etc/restic-rest-server/htpasswd controltower
   sudo htpasswd -B    /etc/restic-rest-server/htpasswd brick
   sudo chmod 0600 /etc/restic-rest-server/htpasswd
   ```

4. **Env file** — Copy `deploy/fleet-backup/rest-server.env.example` to `/etc/restic-rest-server/env` (mode `0600`) and adjust paths if needed.

## Install (DeskComputer WSL)

From the Runner Dashboard deploy tree:

```bash
cd ~/actions-runners/dashboard   # or your RUNNER_DASHBOARD_DEPLOY_DIR
sudo bash deploy/fleet-backup/install-rest-server.sh
sudo systemctl enable --now restic-rest-server.service
sudo systemctl status restic-rest-server.service
```

Verify binding (should show the `100.x` address, not `0.0.0.0`):

```bash
ss -ltnp | grep 8000
tailscale ip -4
```

## Client URL shape (later PRs)

Each client initializes against:

```text
rest:http://<deskcomputer-tailscale-dns-or-ip>:8000/<rest_repo_user>
```

Use `restic -r … init` once per client repo; passwords are **not** shared across machines.

## Rollout

After merge, **Runner Dashboard auto-deploy does not install this service** — run `install-rest-server.sh` once on DeskComputer. Downstream fleet nodes pick up client scripts in `#4011` / `#4012`.

## Related issues

- `#4016` — this hub deployment
- `#4011` — OGLaptop pilot
- `#4012` — fleet rollout after pilot
- `#4009`–`#4013` — other backup panel tracks (USB, drills, brick cleanup)
