# Runner registration purge — detection and recovery

## What happens

GitHub automatically **deletes** the server-side registration of any
self-hosted runner that has not connected for ~14 days. The local install
is untouched, so the failure mode is quiet and confusing:

- the runner disappears from `gh api orgs/D-sorganization/actions/runners`
  entirely (not "offline" — gone);
- the local systemd unit / listener starts fine, then exits with
  `Failed to create a session. The runner registration has been deleted
from the server, please re-configure.` (journal / `_diag/Runner_*.log`);
- units flip to `inactive`/`failed` ("no retry needed") and stay down.

This hit the whole DeskComputer pool (8 runners) and ControlTower's
`matlab-1` on 2026-07-30, after their hosts/distros had been down since
mid-July. The fleet ran on ~8 saturated CT-SSD runners and CI queued for
hours. `deploy/fleet-health-monitor.ps1` now alarms on the purge signature
(zero pool members online while local units run) and enforces per-pool
online floors so silent decay is caught in minutes, not weeks.

## Recovery (per runner)

Mint one org registration token (valid ~1 h, reusable across runners):

```bash
gh api -X POST orgs/D-sorganization/actions/runners/registration-token --jq .token
```

Then, in each runner directory (Linux example; same flow with `config.cmd`
on Windows):

```bash
sudo systemctl stop <unit>
mv .service .service.keep 2>/dev/null          # see gotcha 1
mv .runner_migrated .runner_migrated.bak 2>/dev/null   # see gotcha 2
rm -f .runner .credentials .credentials_rsaparams
./config.sh --unattended --replace \
    --url https://github.com/D-sorganization \
    --token <TOKEN> \
    --name <original-runner-name> \
    --labels d-sorg-fleet,d-sorg-fleet-docker \
    --work _work            # use the pool's label set (see machine_registry.yml)
mv .service.keep .service 2>/dev/null
sudo systemctl restart <unit>
```

`scratch` automation used on 2026-07-30 lives in this repo's history as the
per-host scripts; the canonical per-pool label sets are in
`backend/machine_registry.yml`.

### Gotchas (all hit live on 2026-07-30)

1. **`.service` marker**: `config.sh` refuses to configure (and `remove`
   demands a service uninstall) while the `svc.sh` marker exists — but
   `svc.sh uninstall`/`install` would regenerate the systemd unit and lose
   fleet hardening (Restart=always, KillMode=mixed, hook envs). Move the
   marker aside and restore it instead.
2. **`.runner_migrated`**: the broker-flow copy of `.runner` also trips the
   "already configured" check. Move it aside; a fresh one is written on the
   next session.
3. **Same names + `--replace`**: re-registering under the original names
   keeps unit files, hooks, and dashboards consistent. Never copy `.runner`
   identity files between machines or pools.
4. **ControlTower**: never run `wsl.exe` over SSH (hangs the network-logon
   session). Drive WSL via an interactive scheduled task (wrap it in
   `run-hidden.vbs` so it stays windowless) that writes output to a file.
5. **OGLaptop**: the console may be logged off, so `/IT` tasks won't run —
   use an S4U task (the resident keepalive proves S4U → WSL works there).
6. **Quarantine**: check runner labels before re-registering. A pool
   labelled `d-sorg-quarantine` (ControlTower-NVMe as of 2026-07-30, see
   Runner_Dashboard issue #1071) is out of service **on purpose** — its
   distro must stay stopped and its purged runners must NOT be re-registered
   until the documented reprovision completes.

## Verify

```bash
gh api orgs/D-sorganization/actions/runners --paginate \
  --jq '.runners[] | .name + " " + .status'
```

All expected names present and `online`; `systemctl is-active` on each unit;
the fleet-health-monitor log shows pool counts at/above floors.

## Prevention

- `deploy/fleet-health-monitor.ps1` (DeskComputer, every 5 min): per-pool
  floors, Desktop in-place unit self-heal, purge alarm.
- Keepalives hold distros resident: `RunnerDashboard-WSL-Resident-KeepAlive`
  (S4U) per host / `ControlTower-SSD-KeepAlive`. A distro with runners but
  no keepalive is how the 14-day clock starts.
