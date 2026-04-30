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

## Postmortem Template

[`postmortem-template.md`](./postmortem-template.md)
