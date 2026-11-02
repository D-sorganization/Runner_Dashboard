# Hub tailnet reachability with loopback bind

## Problem

Release `2026.11.01-cf3d18c` and PR #3793 (#3744) require hubs with
`DASHBOARD_LOOPBACK_AUTH=1` to bind **loopback only** (`127.0.0.1`). Uvicorn
no longer listens on the tailnet interface, so peer heartbeats to
`http://<host>:8321` fail unless something else exposes the process on the
tailnet.

Symptoms on a hub:

- `staff.fleet: peer <name> board unavailable` for every peer
- Staff board / fleet status shows only the local machine online

## Supported fix (native Linux)

Use **`tailscale serve`** to proxy the tailnet port to loopback — never bind
`0.0.0.0` in uvicorn.

`deploy/hub_tailnet_serve.py` idempotently runs (when loopback auth is on and
the host is **not** WSL-mirrored):

```bash
tailscale serve --http <port> --bg --yes http://127.0.0.1:<port>
```

WSL-mirrored hosts continue to use `deploy/wsl-mirrored-port-helper.sh`
(Windows `tailscale.exe`).

## Wiring

- `runner-dashboard.service` `ExecStartPost` calls `hub_tailnet_serve.py ensure`
  after the dashboard binds.
- `setup.sh`, `update-deployed.sh`, and `node-self-heal.sh` run the same ensure
  step on deploy and on healthy periodic self-heal.

## Operator checks

```bash
grep -E '^(DASHBOARD_LOOPBACK_AUTH|DASHBOARD_HOST)=' ~/.config/runner-dashboard/env
tailscale serve status
curl -fsS --max-time 5 -H "Authorization: Bearer $HUB_FLEET_TOKEN" \
  -H "X-Requested-With: XMLHttpRequest" \
  "http://127.0.0.1:8321/api/staff/board?local=1" >/dev/null
# From another fleet node (replace URL):
curl -fsS --max-time 8 -H "Authorization: Bearer $HUB_FLEET_TOKEN" \
  -H "X-Requested-With: XMLHttpRequest" \
  "http://peer-host.fleet-example.ts.net:8321/api/staff/board?local=1"
```

Hub auth stays enforced: peers must present `HUB_FLEET_TOKEN` (or another
accepted fleet credential); loopback auth does not bypass peer routes.

See also: `docs/wsl-mirrored-port-conflict.md` for the WSL Windows-side bridge.
