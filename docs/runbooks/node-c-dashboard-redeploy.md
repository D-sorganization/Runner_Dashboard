# node-c Dashboard Redeploy

Native Linux fleet node **node-c** (`node-c.fleet-example.ts.net`, dashboard port **8321**).
The dashboard directory is a deployed artifact and does not contain a Git checkout.

## Preconditions

- Tailscale is connected on node-c (`tailscale status` shows `100.64.254.14`).
- SSH or console access is available as the dashboard operator user.
- The `runner-dashboard.service` user unit is installed and the user's systemd manager is reachable.
- In a headless shell, set `XDG_RUNTIME_DIR` before invoking the updater. Set
  `DBUS_SESSION_BUS_ADDRESS` only if this system's user manager requires it; the
  unit availability check is authoritative without that variable:

  ```bash
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
  systemctl --user status runner-dashboard.service --no-pager
  ```

- Obtain the approved dashboard release artifact and its published SHA-256 value. Keep the downloaded artifact outside `~/actions-runners/dashboard` until the updater starts.
- Obtain the deployment tooling from a separate source checkout outside the deployed directory, pinned to the exact reviewed commit on protected `main` or an approved release tag. Confirm that revision contains the validated `--scope` option. The updater currently in node-c's deployed artifact may predate this option and silently select system scope; do not use it for this first update.
- GitHub App runtime credentials are a separate prerequisite for GitHub API access. Configure `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, and `GITHUB_APP_PRIVATE_KEY_FILE` in the user service's protected environment file, with the PEM outside the artifact directory. Follow [GitHub App Auth Runbook](./github-app-auth.md); never put credentials in the artifact or repository.

## 1. Deploy From a Verified Artifact

Create or refresh a separate source checkout, then detach it at the exact reviewed protected-main commit or approved release tag. Verify the resulting SHA against the revision approved for this update:

```bash
mkdir -p ~/src
git clone --no-checkout https://github.com/D-sorganization/Runner_Dashboard.git \
  ~/src/runner-dashboard-deploy
git -C ~/src/runner-dashboard-deploy fetch --tags origin main
git -C ~/src/runner-dashboard-deploy checkout --detach <reviewed-commit-or-tag>
git -C ~/src/runner-dashboard-deploy rev-parse HEAD
```

If the checkout already exists, fetch the approved ref and detach at the same reviewed revision. From that checkout, invoke its updater and point it at the separately staged artifact. The checksum is checked by the artifact installer, and the updater creates a backup before installation:

```bash
bash ~/src/runner-dashboard-deploy/deploy/update-deployed.sh \
  --scope user \
  --artifact /path/to/dashboard-artifact.tar.gz \
  --checksum <published-sha256>
```

Replace the example checkout path with the external checkout pinned above. Keep
that checkout and artifact outside `~/actions-runners/dashboard`; the deployed
artifact directory itself has no `.git` metadata.

The user scope restarts and checks only `runner-dashboard.service` through
`systemctl --user`. It does not configure privileged host assets or manipulate
runner units. The updater verifies dashboard health and restores the backup
through the user service manager if health verification fails.

After an approved deployment, inspect the live result separately:

```bash
systemctl --user status runner-dashboard.service --no-pager
curl -fsS http://127.0.0.1:8321/api/health | jq .
```

An artifact update does not configure GitHub App credentials. Confirm
`github_auth_source`, `github_auth_status`, and `github_api` in the live health
response; documentation or mocked tests do not establish deployment or
authentication acceptance.

## 2. Register Runners

Use the approved runner installation procedure and node-c's labels from
`machine_registry.yml` (`d-sorg-fleet`, `d-sorg-fleet-docker`, and others).
After registration, `/api/runners` should list active runners on node-c.

## 3. Verify Dispatch With a Dry Run

From the **hub** (hub-a) Staff Console or API:

```bash
curl -sS -X POST "http://hub-a.fleet-example.ts.net:8321/api/staff/ad-hoc/run" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"prompt":"registry smoke","provider":"codex","machine":"node-c","dry_run":true}'
```

Expect a planned run targeting machine `node-c` without executing on the hub.

## Rollback

The updater automatically restores its timestamped backup when the health gate
fails. For a manual rollback, use the matching backup and the same active user
manager environment:

```bash
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
bash ~/src/runner-dashboard-deploy/deploy/rollback-deployed.sh \
  --backup-dir /path/to/dashboard.bak.<timestamp> \
  --deploy-dir ~/actions-runners/dashboard \
  --scope user
```
