# Deployment Model -- Runner Dashboard

This guide covers the full operational lifecycle of the Runner Dashboard: initial
setup, routine updates, dry-run preview, rollback, secrets management, token
refresh, health verification, artifact-based deployment, and multi-machine fleet
operation.

---

## Overview

The Runner Dashboard is a FastAPI backend + React SPA frontend that runs as a
**systemd service** (`runner-dashboard.service`) on each fleet machine. It
exposes a web UI on port 8321 and proxies the GitHub API for runner and workflow
management.

The service is long-lived: you update it in-place using the deploy scripts rather
than restarting the machine.

---

## File Layout

After a successful setup, the deployed dashboard lives at:

```
~/actions-runners/dashboard/
|-- backend/          # Python FastAPI application (server.py, modules)
|-- frontend/         # Static SPA (index.html)
|-- config/           # Runtime configuration (agent_remediation.json, etc.)
|-- local_apps.json   # Local process health registry
|-- deployment.json   # Version and deployment metadata
`-- refresh-token.sh  # GitHub token refresh helper
```

Secrets are stored separately from the deploy tree:

```
~/.config/runner-dashboard/env   # GH_TOKEN, GITHUB_ORG, etc. -- loaded by systemd
```

---

## Bootstrap (First Time)

Run `setup.sh` once per machine. Provide the machine-specific flags:

```bash
# ControlTower -- 8 runners, hub role
bash deploy/setup.sh --runners 8 --machine-name ControlTower --role hub

# OG-Laptop -- 4 runners, node role (default)
bash deploy/setup.sh --runners 4 --machine-name OG-Laptop

# Brick-Windows -- 1 runner, GPU node
bash deploy/setup.sh --runners 1 --machine-name Brick-Windows

# DeskComputer -- schedule-controlled runner count
bash deploy/setup.sh --runners 8 --machine-name DeskComputer --runner-aliases desktop
```

`setup.sh` will:
1. Install Python dependencies.
2. Copy the dashboard to `~/actions-runners/dashboard/`.
3. Write the systemd unit file and enable the service.
4. Configure sudoers so the runner user can control runner services.

After all machines are running, add the fleet node list to the hub:

```bash
bash deploy/setup.sh --runners 8 --machine-name ControlTower --role hub \
  --fleet-nodes "Brick-Windows:http://100.x.x.x:8321,OG-Laptop:http://100.x.x.x:8321"
```

---

## Routine Update

After any change to `backend/`, `frontend/`, or `local_apps.json`, push the
update to the deployed instance:

```bash
bash deploy/update-deployed.sh
```

The script will:
1. Install/update Python dependencies via `pip_install` (sourced from `deploy/lib.sh`).
2. Create a timestamped backup of the current deploy directory (`.bak.YYYYMMDD_HHMMSS`).
3. Copy updated backend, frontend, deploy helpers, and `local_apps.json`.
4. Write fresh `deployment.json` metadata.
5. Restart `runner-dashboard.service`.
6. Verify the service started and check GitHub API connectivity.
7. Smoke-test the `/api/runs` endpoint.

### Override the Source Repo Path

If the repo is not at the default Windows path:

```bash
bash deploy/update-deployed.sh --repo /path/to/runner-dashboard-parent
```

### Override the Deploy Directory

```bash
bash deploy/update-deployed.sh --deploy-dir /custom/deploy/path
```

---

## Dry-Run Mode

Preview every destructive step without executing it:

```bash
bash deploy/update-deployed.sh --dry-run
```

Or via environment variable:

```bash
DRY_RUN=true bash deploy/update-deployed.sh
```

In dry-run mode:
- No backup is created.
- No files are copied or synced.
- `systemctl restart` is not called.
- The health check and smoke tests are skipped.
- Each skipped step is logged as `[WARN] [DRY-RUN] Skipping: <description>`.

---

## Rollback

Each run of `update-deployed.sh` automatically creates a timestamped backup
of the deploy directory before any files are changed. Use `rollback.sh` to
restore a previous state.

### List Available Backups

```bash
bash deploy/rollback.sh --list
```

Output example:

```
[INFO] Available backups:
/home/user/actions-runners/dashboard.bak.20260423_141502
/home/user/actions-runners/dashboard.bak.20260422_093017
```

### Roll Back to the Most Recent Backup

```bash
bash deploy/rollback.sh
```

The script auto-selects the most recent backup, restores files, and restarts
the service.

### Roll Back to a Specific Backup

```bash
bash deploy/rollback.sh --to /home/user/actions-runners/dashboard.bak.20260422_093017
```

### Dry-Run Rollback

```bash
bash deploy/rollback.sh --dry-run
```

---

## Secrets

The systemd service loads environment variables from:

```
~/.config/runner-dashboard/env
```

Required variables:

| Variable | Description |
|---|---|
| `GH_TOKEN` | GitHub PAT with `admin:org`, `repo`, `workflow` scopes |
| `GITHUB_ORG` | GitHub organization name (e.g. `D-sorganization`) |
| `NUM_RUNNERS` | Expected runner count for this machine |
| `DISPLAY_NAME` | Human-readable machine name shown in the UI |

Do not commit this file -- it is outside the repo tree and loaded only by systemd
at service start. The file is owned by the runner user with mode `0600`.

---

## Token Refresh

GitHub PATs expire. When the dashboard reports `github_api: disconnected`, refresh
the token:

```bash
bash deploy/refresh-token.sh
```

Or manually:

```bash
TOKEN=$(gh auth token 2>/dev/null)
sed -i '/^GH_TOKEN=/d' ~/.config/runner-dashboard/env
printf 'GH_TOKEN=%s\n' "$TOKEN" >> ~/.config/runner-dashboard/env
sudo systemctl restart runner-dashboard
```

If `gh auth token` returns empty, re-authenticate first:

```bash
gh auth login
gh auth refresh -s admin:org
```

---

## Health Check

Verify the service is responding:

```bash
curl http://localhost:8321/api/health
```

Expected response when fully connected:

```json
{"status": "ok", "github_api": "connected", "runners_registered": 8}
```

Check the detailed system view:

```bash
curl -s http://localhost:8321/api/health | python3 -m json.tool
```

---

## Artifact-Based Deployment

For release builds, deploy from a pre-built tarball instead of the source
checkout:

```bash
bash deploy/update-deployed.sh --artifact runner-dashboard-v4.0.1.tar.gz
```

The `--artifact` flag delegates file installation to
`deploy/install-dashboard-artifact.sh`, which unpacks the tarball into the
deploy directory. The backup, dependency install, and service restart steps
still run normally.

Build artifacts are described in `deploy/ARTIFACT_BUILD.md`.

---

## Multi-Machine Fleet

Each fleet machine runs its own dashboard instance independently. There is no
shared database or central coordinator -- the hub machine (`--role hub`) aggregates
status from the other nodes via the `/api/fleet/nodes` endpoint using Tailscale
IPs.

Setup order:

1. Run `setup.sh` on every node machine.
2. Note each node's Tailscale IP.
3. Run `setup.sh` on the hub machine with `--role hub --fleet-nodes "..."`.

Updates are deployed per-machine. There is no fleet-wide push mechanism built
into the deploy scripts; use the Fleet Orchestration tab in the dashboard UI
for coordinated rolling deploys.

---

## Troubleshooting

### View Live Logs

```bash
sudo journalctl -u runner-dashboard -f
```

### View Recent Logs (No Pager)

```bash
sudo journalctl -u runner-dashboard -n 50 --no-pager
```

### Check Service Status

```bash
sudo systemctl status runner-dashboard
```

### Service Fails to Start After Update

1. Check the journal for the Python traceback.
2. Verify the env file: `cat ~/.config/runner-dashboard/env`
3. Roll back: `bash deploy/rollback.sh`
4. Investigate the change that broke the service, fix, and redeploy.

### PWA Launcher Recovery (One-Click Service Restart)

The dashboard can be installed as a Progressive Web App (PWA). If the backend service crashes or stops, the PWA shows a recovery modal with platform-specific recovery options.

**Windows/macOS:**
- Click "Start Now" to trigger the custom URL protocol handler (`runner-dashboard://start`)
- The launcher script will start the backend service and open the dashboard

**Linux:**
- Use the "Refresh" button and run the systemctl command shown in the modal:
  ```bash
  systemctl --user restart runner-dashboard
  ```
- Or start the system service:
  ```bash
  sudo systemctl restart runner-dashboard
  ```

**Launcher logs** (all platforms):
```bash
cat ~/.config/runner-dashboard/launcher.log
```

These logs record all recovery attempts, including timestamps, health check results, and success/failure status. Useful for troubleshooting repeated service failures.

### GitHub API Rate Limit

The dashboard shows `github_api: rate_limited` when the 5000 req/hr limit is
exhausted. This clears automatically when the rate-limit window resets. Check
the reset time:

```bash
curl -s http://localhost:8321/api/health | python3 -m json.tool
```

---

## Shared Deploy Library

All deploy scripts source `deploy/lib.sh` for shared utilities:

| Function | Description |
|---|---|
| `ok`, `info`, `warn`, `fail` | Coloured log output |
| `require_dir`, `require_file`, `require_cmd` | Guard assertions |
| `pip_install <pkg...>` | pip3 install with `--break-system-packages` when supported |
| `sync_dir <src> <dest>` | rsync with rm/cp fallback |
| `backup_dir <path>` | Timestamped `cp -a` backup; prints backup path |
| `dry_run "<description>"` | Returns 0 (skip) when `DRY_RUN=true` |

To add a new deploy script, start with:

```bash
#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=deploy/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
```
