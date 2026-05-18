# Fleet rollout: runner corruption fixes (PRs #660–#664)

This runbook walks an operator (or agent on each host) through applying
the four-PR root-cause fix for [#651](https://github.com/D-sorganization/Runner_Dashboard/issues/651)
to every machine in the fleet.

## Series

| PR | What it ships | Where it lands |
|----|---------------|----------------|
| [#660](https://github.com/D-sorganization/Runner_Dashboard/pull/660) | Cleanup strict-mode fix | `deploy/runner-cleanup.sh` → `/usr/local/bin/runner-cleanup` |
| [#661](https://github.com/D-sorganization/Runner_Dashboard/pull/661) | Operator break-glass `heal-host.sh` + runbook | `deploy/heal-host.sh` |
| [#664](https://github.com/D-sorganization/Runner_Dashboard/pull/664) | Job-pickup lockfile + `KillMode=mixed` (root cause) | `deploy/migrate-runner-units.sh`, `deploy/runner-hooks/*`, autoscaler + cleanup logic |
| [#663](https://github.com/D-sorganization/Runner_Dashboard/pull/663) | Prometheus metrics for cleanup / corruption residue / orphan workers | `deploy/runner-corruption-scan.sh`, `deploy/observability/vector.toml` |

These should all merge in any order. The rollout below assumes all four are on `main`.

## Fleet inventory

As of 2026-05-18 the fleet is:

| Host                      | Runners | Platform        | Notes |
|---------------------------|---------|-----------------|-------|
| `d-sorg-local-ControlTower` | 16    | WSL2 (Ubuntu)   | Confirmed corruption 2026-05-18, healed manually |
| `d-sorg-local-Desktop`    | 8       | WSL2 (Ubuntu)   | Same layout — assume corruption present |
| `d-sorg-local-Oglaptop`   | 8       | Linux           | Verify before rollout |
| `d-sorg-local-Brick`      | 1       | Linux           | Verify before rollout |
| `ControlTower-MATLAB`     | 1       | Windows         | NOT AFFECTED — different code path |

Skip the Windows host; the bug is Linux-specific (`KillMode=process`, journald, `_diag/pages` filesystem layout).

## Per-host rollout — happy path

Run as the operator user (sudo will be invoked as needed). The agent on each host should follow this same sequence.

### 1. Pull the latest dashboard deploy artifacts

```bash
cd /opt/runner-dashboard
sudo -u $(stat -c %U .) git fetch --quiet origin main
sudo -u $(stat -c %U .) git reset --hard origin/main
sudo deploy/update-deployed.sh          # restarts runner-dashboard.service and runner-autoscaler.service
```

Verify the new files landed:

```bash
ls -la deploy/heal-host.sh \
       deploy/migrate-runner-units.sh \
       deploy/runner-hooks/job-started.sh \
       deploy/runner-hooks/job-completed.sh \
       deploy/runner-corruption-scan.sh
sha256sum /usr/local/bin/runner-cleanup   # expect a new hash; the Apr 23 binary is stale
```

### 2. Apply the systemd drop-in (#664)

```bash
sudo deploy/migrate-runner-units.sh --dry-run   # preview
sudo deploy/migrate-runner-units.sh             # apply
```

This writes `/etc/systemd/system/actions.runner.<...>.service.d/10-runner-dashboard-busy-lock.conf` for every runner unit. The changes take effect on the next service restart (next step). Idempotent — safe to re-run.

### 3. Heal the host (#661)

```bash
sudo deploy/heal-host.sh
```

This will:

1. Drain every `actions.runner.*.service` (with cgroup-wide pkill belt-and-suspenders).
2. GC stale lockfiles, `_runner_file_commands`, and `_diag/pages` logs.
3. Restart every unit, picking up the new `KillMode=mixed` drop-in.

Verify all units came back:

```bash
systemctl list-units --type=service --state=active 'actions.runner.*.service' \
  | grep -c '^\s*actions\.runner\.'
```

Expected: count equals the number of runners on this host.

### 4. Install corruption-scan metrics (#663)

```bash
sudo deploy/install-runner-maintenance.sh   # extends to install the new timer
systemctl status runner-corruption-scan.timer
```

The scan publishes textfile-collector metrics every 5 minutes. If your host runs `node_exporter` with the textfile collector, scrape will be automatic.

### 5. Sanity-check

```bash
# Cleanup result metric should appear once the next nightly run fires.
cat /var/log/runner-cleanup/last-run.prom 2>/dev/null || echo "no run yet (expected before next 04:20 cycle)"

# Corruption gauge should be at 0 right after heal.
cat /var/run/runner-corruption.prom 2>/dev/null | grep -E 'runner_corruption_residue_count'

# Listener log should show 'Listening for Jobs' on every runner.
sudo journalctl -u 'actions.runner.*' --since '2 minutes ago' \
  | grep -c 'Listening for Jobs'
```

### 6. Cancel + re-trigger any stuck jobs

If jobs were assigned to this host's runners before the rollout, they may be hung. Cancel and re-run via the dashboard's Workflows tab, or:

```bash
# List runs assigned to this host's runners
gh api orgs/D-sorganization/actions/runners --paginate \
  --jq '.runners[] | select(.name | startswith("d-sorg-local-<hostname>-")) | {id, name, busy}'

# For each busy run, cancel + rerun
gh api -X POST "repos/<org>/<repo>/actions/runs/<run_id>/cancel"
gh api -X POST "repos/<org>/<repo>/actions/runs/<run_id>/rerun"
```

## Per-host rollout — when things go wrong

### Symptom: `heal-host.sh` reports units failing to restart

Check the specific unit's journal:

```bash
sudo journalctl -u actions.runner.<...>.service -n 50
```

Common causes:
- Token expired → `sudo deploy/refresh-token.sh` on this host.
- Disk full → `sudo /usr/local/bin/runner-cleanup --compact-vhd-only` (WSL hosts).
- Drop-in syntax error → `sudo rm /etc/systemd/system/actions.runner.<...>.service.d/10-runner-dashboard-busy-lock.conf` and re-run `migrate-runner-units.sh`.

### Symptom: corruption returns within 24h

This means PR #664's race fix didn't fully close the window on this host. Confirm:

```bash
# Inspect a corruption incident — when was the lockfile written?
ls -la /var/run/runner-busy/

# Did the autoscaler see it? Recent decision log.
sudo journalctl -u runner-autoscaler.service --since '1 hour ago' \
  | grep -E 'lockfile|busy|stop'
```

If the autoscaler is still stopping units with fresh lockfiles, file a follow-up against `D-sorganization/Runner_Dashboard` with the journal excerpt.

### Symptom: dashboard shows "Backend is down" + the recovery dialog mentions HTTPS

Apply PR [#662](https://github.com/D-sorganization/Runner_Dashboard/pull/662) (it fixes the misleading error message). The actual reason the backend health check fails is usually one of:

1. The browser is loading the dashboard from `http://<host>` instead of the Tailscale Funnel HTTPS URL — load the Funnel URL instead.
2. `runner-dashboard.service` is genuinely down: `sudo systemctl status runner-dashboard.service`. Restart: `sudo systemctl restart runner-dashboard.service`.
3. The Funnel itself is misrouting `/api/*` — check `tailscale funnel status` against `docs/tailscale-funnel.md`.

### Rollback

If the migration drop-in introduces a regression on a host:

```bash
sudo rm /etc/systemd/system/actions.runner.*.service.d/10-runner-dashboard-busy-lock.conf
sudo systemctl daemon-reload
sudo deploy/heal-host.sh
```

The runner behaviour reverts to pre-#664 — orphan-Worker risk returns but
nothing is permanently broken. Then file an issue with the failure mode.

## Verification across the fleet

Once all hosts are rolled out, sanity-check from a workstation:

```bash
# All runners should be online, distribution of busy:idle should be sane.
gh api orgs/D-sorganization/actions/runners --paginate \
  --jq '[.runners[] | select(.name | startswith("d-sorg-local-"))]
        | group_by(.name | sub("-[0-9]+$"; ""))
        | map({host: .[0].name | sub("-[0-9]+$"; ""), online: ([.[] | select(.status=="online")] | length), busy: ([.[] | select(.busy)] | length), total: length})'
```

Expected output: each host has `online == total` (all listeners up). Busy count varies with workload.

```bash
# No more "remains running after unit stopped" events in the last 24h
# (this is the orphan-Worker telltale that #664 prevents):
for host in d-sorg-local-ControlTower d-sorg-local-Desktop d-sorg-local-Oglaptop d-sorg-local-Brick; do
  ssh "$host" "sudo journalctl --since '24 hours ago' \
    | grep -c 'Runner.Worker.*remains running after unit stopped'" 2>/dev/null \
    | xargs -I{} echo "$host: {} orphan events"
done
```

After rollout the expected count is **0** per host (it was 14+/day on `ControlTower` before the fix).

## Per-host agent prompt template

If you're delegating rollout to an agent on each host, paste this prompt (substitute `<HOST>`):

> You are an operator agent on `<HOST>`. The Runner_Dashboard repo has shipped four PRs (#660, #661, #663, #664) that fix the recurring runner-corruption pattern documented in issue #651. Apply them on this host by following `docs/runbooks/fleet-rollout-corruption-fixes.md` exactly. Run each step, capture stdout, and reply with a short status report covering: (1) which step you reached, (2) whether all `actions.runner.*.service` units are active, (3) the output of the verification section, (4) any errors encountered. Do not deviate from the runbook — if a step fails, stop and report.

## Cross-references

- Issue: [#651](https://github.com/D-sorganization/Runner_Dashboard/issues/651)
- Per-host heal runbook: [`runner-corruption-heal.md`](runner-corruption-heal.md)
- Observability doc (metrics from #663): [`observability.md`](../observability.md)
- Memory note: `runner_corruption_pattern.md`
