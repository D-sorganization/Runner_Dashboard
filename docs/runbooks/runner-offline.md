# Runbook: Runner Offline

One or more self-hosted GitHub Actions runners in the D-sorganization fleet
are reporting `offline` to GitHub or are not picking up jobs.

## Symptom

- The Fleet tab in the dashboard shows runners with red/offline status.
- GitHub repo settings page shows runners as `Offline`.
- Workflow runs sit `queued` indefinitely with no assignment.
- Operators report jobs that should match a runner label never start.
- `journalctl` for `actions.runner.*` reports auth errors or missing token.

## Severity

**P1** if the entire fleet (or the only runner servicing a critical label)
is offline — CI is fully blocked.
**P2** if at least one runner per required label is still online and queue
depth is bounded.

## Detection

- Dashboard Fleet tab marks the row red and surfaces the last-seen timestamp.
- `gh api orgs/D-sorganization/actions/runners` shows `status: "offline"`.
- Queue Health tab shows growing `queued` count without a matching
  `in_progress` increase.
- `systemctl status 'actions.runner.*'` on the host reports `failed` or
  `inactive`.

## Diagnosis

Run on the host that owns the affected runner(s):

```bash
# 1. Which runner units exist and what state are they in?
systemctl list-units 'actions.runner.*' --all --no-pager

# 2. Tail logs for the unhealthy runner (replace name accordingly).
sudo journalctl -u 'actions.runner.D-sorganization-*.service' -n 200 --no-pager

# 3. Cross-check what GitHub thinks of the fleet.
gh api orgs/D-sorganization/actions/runners \
  --jq '.runners[] | {name, status, busy, labels: [.labels[].name]}'

# 4. Confirm the host can reach GitHub.
curl -fsS -m 5 https://api.github.com/zen

# 5. Check the dashboard's view of fleet health.
curl -fsS http://localhost:8321/api/health
curl -fsS http://localhost:8321/api/fleet/runners | jq '.[] | {name,status,busy}'

# 6. If autoscaler is in use, confirm it is running.
sudo systemctl status runner-autoscaler --no-pager
```

## Mitigation

```bash
# A. Restart the offending runner unit (most common fix).
sudo systemctl restart 'actions.runner.D-sorganization-<repo>.<runner>.service'

# B. If the unit is missing or auth is broken, re-register the runner.
#    Pull a fresh registration token via gh, then run the runner's config.sh.
gh api -X POST orgs/D-sorganization/actions/runners/registration-token \
  --jq .token
cd ~/actions-runners/<runner-dir>
./config.sh remove --token <removal-token>   # if previously registered
./config.sh --url https://github.com/D-sorganization \
  --token <registration-token> \
  --labels <labels> --unattended --replace
sudo systemctl restart 'actions.runner.*'

# C. If multiple runners are offline simultaneously, suspect token/network.
#    Refresh the dashboard token and confirm Tailscale is up.
bash ~/actions-runners/dashboard/deploy/refresh-token.sh
tailscale status | head
```

If runners cannot be brought back quickly, announce a CI freeze in the
operator channel and pause merges that depend on the affected labels.

## Resolution

- If the cause was an expired PAT, rotate the org-level token, store it in
  `~/.config/runner-dashboard/env` (chmod 600), and update any per-runner
  registration tokens. Add a calendar reminder for the next rotation.
- If a deploy or OS update removed the runner unit, restore via
  `deploy/setup.sh` and re-register the runner.
- If runners drop offline repeatedly, capture the failure window from
  `journalctl` and open a P2 issue. Consider adding a watchdog in
  `backend/runner_autoscaler.py` to detect and respawn.
- Update `backend/machine_registry.yml` if a node is permanently retired.
- File a postmortem when more than one runner went offline simultaneously.

## Postmortem Template

[`postmortem-template.md`](./postmortem-template.md)
