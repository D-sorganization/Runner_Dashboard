# Runbook: Dashboard Down

The Runner Dashboard FastAPI service on port 8321 is unreachable or returning
5xx for `/api/health`.

## Symptom

- Browser tab at `http://localhost:8321/` (or the Tailscale URL) shows
  "connection refused", "502 Bad Gateway", or hangs indefinitely.
- `/api/health` returns 5xx, never returns, or `curl` cannot connect.
- Other operators report the dashboard is unreachable.
- `docs/fleet-in-flight.md` and other dashboard-driven artifacts stop updating.

## Severity

**P1** — the dashboard is the primary operator console for the entire fleet.
While it is down, operators cannot dispatch agents, monitor runners, or purge
stale queues from the UI. CI and runners themselves may keep functioning, but
visibility is lost.

## Detection

- `systemctl status runner-dashboard` reports `failed`, `inactive`, or
  repeated `Restart=always` loops.
- Browser monitoring or a teammate reports `localhost:8321` unreachable.
- `curl -fsS http://localhost:8321/api/health` returns non-2xx or fails.
- Push notifications subscribed to operator topics stop arriving.
- `journalctl -u runner-dashboard` shows a Python traceback at the tail.

## Diagnosis

Run these in order on the host running the dashboard service:

```bash
# 1. Is the systemd unit running?
sudo systemctl status runner-dashboard --no-pager

# 2. What does the service log say (last 200 lines)?
sudo journalctl -u runner-dashboard -n 200 --no-pager

# 3. Is anything actually listening on port 8321?
sudo ss -tlnp | grep 8321 || echo "nothing on 8321"

# 4. Does the health endpoint respond locally?
curl -fsS -m 5 http://localhost:8321/api/health || echo "health failed"

# 5. Was the host recently updated? Check VERSION and last deploy.
cat ~/actions-runners/dashboard/VERSION 2>/dev/null
ls -dt ~/actions-runners/dashboard.bak.* 2>/dev/null | head -3

# 6. Is the service env file readable and intact?
sudo -u "$(whoami)" test -r ~/.config/runner-dashboard/env && echo "env ok" \
  || echo "env missing or unreadable"
```

## Mitigation

Stop-the-bleeding, in order of preference:

```bash
# A. Restart the service (works for transient crashes)
sudo systemctl restart runner-dashboard
sleep 3
sudo systemctl is-active runner-dashboard && curl -fsS http://localhost:8321/api/health

# B. If restart fails, roll back to the previous deployed snapshot.
#    See deploy/rollback.sh; it auto-selects the most recent .bak.* directory.
bash ~/actions-runners/dashboard/deploy/rollback.sh --list
bash ~/actions-runners/dashboard/deploy/rollback.sh

# C. If port 8321 is held by a stale process (no systemd entry), kill it
#    and let systemd restart cleanly.
sudo fuser -k 8321/tcp || true
sudo systemctl start runner-dashboard
```

If the dashboard cannot recover, announce in the operator channel that the
console is down and direct teammates to operate runners and CI directly via
`gh` and `systemctl` until restored.

## Resolution

After mitigation, investigate the **root cause** before closing:

- If `journalctl` shows a traceback, file a P1 issue with the trace and the
  commit SHA from `~/actions-runners/dashboard/VERSION`. Revert the offending
  change, then ship a forward fix with a regression test under `tests/api/`.
- If the cause was an exhausted token (`GH_TOKEN` expired or revoked),
  rotate via `deploy/refresh-token.sh` and update
  `~/.config/runner-dashboard/env` (chmod 600).
- If `/api/health` is now slow or flaky under load, add a backend test that
  exercises the failing path and consider raising the upstream timeout in
  `backend/middleware.py`.
- Confirm `deploy/scheduled-dashboard-maintenance.sh` ran successfully in
  cron; restore it if the cron entry was lost.
- File a postmortem.

## Pattern: EROFS / Read-only filesystem at startup

A specific class of crash-loop deserves callout because the surface error
message is misleading. If `journalctl -u runner-dashboard -n 100 --no-pager`
shows:

```
OSError: [Errno 30] Read-only file system: '/home/<user>/.local/share/runner-dashboard'
```

(or any other path under `$HOME`), this is **not** a filesystem-permissions
problem on the directory itself. The systemd sandbox sets
`ProtectHome=read-only`, which makes `$HOME` read-only from the service's
view except for explicit `ReadWritePaths=` entries. New code paths that
write to home directories outside that allowlist hit `EROFS`, even if the
target directory is mode `0755` and owned by the service user.

### Quick triage

```bash
# 1. Identify which path the service is failing to write.
journalctl -u runner-dashboard -n 100 --no-pager | grep -B2 -i "Read-only file system"

# 2. Compare against the installed unit's ReadWritePaths.
sudo grep -nE 'ReadWritePaths|ProtectHome' \
    /etc/systemd/system/runner-dashboard.service \
    /etc/systemd/system/runner-dashboard.service.d/*.conf 2>/dev/null
```

### Quick fix (per-host, drop-in — survives `setup.sh` re-runs)

```bash
sudo mkdir -p /etc/systemd/system/runner-dashboard.service.d
sudo tee /etc/systemd/system/runner-dashboard.service.d/99-rw-paths.conf <<EOF
[Service]
ReadWritePaths=/home/$USER/.local/share/runner-dashboard
ReadWritePaths=/home/$USER/actions-runners/dashboard
EOF
sudo systemctl daemon-reload
sudo systemctl restart runner-dashboard
```

Drop-ins are layered additively on top of the main unit; they survive a
`setup.sh` re-install of the main unit file.

### Permanent fix

The deploy template (`deploy/runner-dashboard.service`) carries the canonical
`ReadWritePaths=` list. New code that writes to a path not on the list
should land alongside an update to the template. See
[`docs/deployment-model.md` § "systemd Sandboxing"](../deployment-model.md#systemd-sandboxing)
for the current list.

## Postmortem Template

[`postmortem-template.md`](./postmortem-template.md)
