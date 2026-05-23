# WSL-mirrored port conflict — runbook

## Symptom

After WSL2 cold-starts on a dashboard host that runs Windows-side
`tailscale serve` for the dashboard port (default 8321),
`runner-dashboard.service` enters a systemd restart loop with the
following error in `journalctl -u runner-dashboard`:

```
ERROR:    [Errno 98] error while attempting to bind on address ('0.0.0.0', 8321): address already in use
```

`ss -tlnp` _inside WSL_ shows **no listener** on port 8321. The conflict
is invisible from WSL because it is enforced by the Windows host's
network stack.

## Root cause

`.wslconfig` sets `networkingMode=mirrored`. Under mirrored networking
the Linux guest shares the Windows host's network namespace. Windows
`tailscaled.exe` binds the dashboard port on the Tailscale-assigned IP
(e.g. `100.x.y.z:8321`) as part of the `tailscale serve --tcp 8321`
bridge that exposes the dashboard to the Tailnet. While Windows holds
the port, **any** WSL process that tries to bind the same port number
fails — even on `127.0.0.1`, even on a different specific IP. Plain
Python reproduces the failure:

```bash
$ python3 -c 'import socket
s = socket.socket(); s.bind(("127.0.0.1", 8321))'
OSError: [Errno 98] Address already in use
```

The same WSL process **can** coexist with the Windows listener once the
dashboard has bound first — the kernel allows the parallel bindings once
both are established. Only the initial bind contends.

## Fix

`deploy/wsl-mirrored-port-helper.sh` toggles the Tailscale-serve
binding around the dashboard's bind:

1. **`clear`** — runs as systemd `ExecStartPre`. Calls
   `tailscale.exe serve --tcp=8321 off` so Windows releases the port.
2. **`restore`** — runs as systemd `ExecStartPost`. Polls for the WSL
   listener (uvicorn does not call `sd_notify(READY=1)`, so
   `Type=notify` fires `ExecStartPost` too early), then re-adds
   `tailscale serve --tcp 8321 --bg --yes tcp://127.0.0.1:8321`.

The helper is **idempotent** and a **no-op outside WSL-mirrored
topologies** (non-WSL hosts; WSL hosts without `tailscale.exe`). It is
therefore safe to install on every dashboard host.

```ini
# /etc/systemd/system/runner-dashboard.service (excerpt)
ExecStartPre=/home/USER/actions-runners/dashboard/refresh-token.sh
ExecStartPre=/home/USER/actions-runners/dashboard/wsl-mirrored-port-helper.sh clear --port 8321
ExecStart=/usr/bin/python3.11 /home/USER/actions-runners/dashboard/backend/server.py
ExecStartPost=/home/USER/actions-runners/dashboard/wsl-mirrored-port-helper.sh restore --port 8321
```

## Verifying the fix

Cold-restart WSL and watch the dashboard recover with no manual
intervention:

```powershell
wsl --shutdown
# wait ~15s for systemd to restart the dashboard
wsl -d Ubuntu-22.04 -- curl -sf http://localhost:8321/api/health
```

Inspect the recovery sequence:

```bash
journalctl -u runner-dashboard --since '2 minutes ago' \
  | grep -E 'clear|restore|Network|already in use'
```

A healthy cycle looks like:

```
wsl-mirrored-port-helper.sh: clearing tailscale serve binding for port 8321
wsl-mirrored-port-helper.sh: cleared
python3: 2026-05-22 ... INFO   Network: http://0.0.0.0:8321
wsl-mirrored-port-helper.sh: restored
systemd[1]: Started D-sorganization Runner Dashboard (DeskComputer).
```

## Related

- `deploy/wsl-mirrored-port-helper.sh` — the helper itself.
- `tests/deploy/test_wsl_mirrored_port_helper.py` — argument-parsing
  and idempotence tests (run anywhere bash is available).
- `deploy/wsl-keepalive.ps1` — Windows-side watchdog that probes WSL
  responsiveness (not just `wsl --list` state) and recovers the distro
  itself when it hangs. Independent of this fix but motivated by the
  same recurring-incident postmortem.
