# Brick dashboard redeploy

Native Linux fleet node **Brick** (`brick.tail2bbcc7.ts.net`, dashboard port **8321**).

## Preconditions

- Tailscale connected on Brick (`tailscale status` shows `100.93.80.7`).
- SSH or console access as the dashboard operator user.
- No secrets committed to git; use host-local env and GitHub App / token files only.

## 1. Deploy current `main`

On Brick, from the deployed checkout (typically under `~/actions-runners/dashboard`):

```bash
cd ~/actions-runners/dashboard
git fetch origin main
git checkout main
git pull --ff-only origin main
./deploy/update-deployed.sh
```

Confirm health:

```bash
curl -sS http://127.0.0.1:8321/api/health | jq .
```

Expect `status` not `degraded` once auth and runners are fixed below.

## 2. Fix GitHub auth (no secrets in repo)

Follow [github-app-auth.md](./github-app-auth.md) or refresh the operator token in
`~/.config/runner-dashboard/env` (or the path your unit file uses). Restart the service:

```bash
sudo systemctl restart runner-dashboard.service
```

Verify `github_auth_status` on `/api/health` is healthy.

## 3. Register runners

Use the same runner install path as other Linux nodes (`RUNNER_BASE_DIR`, labels from
`machine_registry.yml` for Brick: `d-sorg-fleet`, `d-sorg-fleet-docker`, etc.).

After registration, `/api/runners` should list active runners on Brick.

## 4. Verify dispatch (dry run)

From the **hub** (DeskComputer) Staff console or API:

```bash
curl -sS -X POST "http://deskcomputer.tail2bbcc7.ts.net:8321/api/staff/ad-hoc/run" \
  -H "Content-Type: application/json" \
  -H "X-Requested-With: XMLHttpRequest" \
  -d '{"prompt":"registry smoke","provider":"codex","machine":"Brick","dry_run":true}'
```

Expect a planned run targeting machine `Brick` without executing on the hub.

## Rollback

Re-run `deploy/update-deployed.sh` from the previous known-good SHA and restart
`runner-dashboard.service`.
