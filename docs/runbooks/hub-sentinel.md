# Hub liveness sentinel (out-of-band, runs on node-c)

The hub (today hub-a:8321) cannot report its own outage: the scheduler,
Postman and push workers all live in its own process. `deploy/hub_sentinel_cli.py`
runs on a **second machine** and probes `$HUB_URL/api/health` (falling back to
`/readyz`) every 5 minutes via `deploy/systemd/hub-sentinel.{service,timer}`.
Probe classification and the alert/cooldown/recovery state machine live in
`deploy/hub_sentinel.py`; the alert sinks and fan-out live in
`deploy/hub_sentinel_alerts.py`.

- **One alert per outage**: after 3 consecutive failed probes (≤15 min with the
  5-minute timer), classified as `transport_down | http_error | not_ready`.
  Later failures in the same outage stay silent.
- **One recovery notice**: the first successful probe after an alerted outage,
  naming the outage duration. A blip that recovers before 3 failures (no alert
  was ever sent) stays silent too.
- **Fails closed against alert storms**: a missing or corrupt state file
  (`~/.config/runner-dashboard/hub_sentinel_state.json`, override with
  `HUB_SENTINEL_STATE`) is treated as "unknown," never as "already mid-outage" —
  it needs 3 fresh failures before it alerts.
- **Alert sinks** (config-selected via env, each optional, independent — one
  failing sink never blocks the others):
  - `HUB_SENTINEL_BARB_INBOX=<path>` — appends one line per alert/recovery to a
    local Barb inbox file.
  - `HUB_SENTINEL_SMTP_TO=<addr>` (+ `HUB_SENTINEL_SMTP_HOST`/`_PORT`/`_FROM`) —
    plain SMTP, off unless set. No sink ever receives or logs a token.
  - `HUB_SENTINEL_RM_REPO=<owner/repo>` (+ `HUB_SENTINEL_RM_ISSUE`, default
    `2539`) — posts a comment via the fleet `gh` wrapper, which mints its own
    App token internally (never passed on our command line).

## Install (manual, one-time per node)

```bash
sudo cp deploy/systemd/hub-sentinel.{service,timer} /etc/systemd/system/
sudo sed -i \
  -e "s/@DASHBOARD_USER@/$(whoami)/" \
  -e "s#@DASHBOARD_HOME@#$HOME#" \
  -e "s#@ENV_FILE@#$HOME/.config/runner-dashboard/env#" \
  -e "s#@PYTHON_BIN@#$(command -v python3)#" \
  -e "s#@HUB_URL@#http://hub-a:8321#" \
  /etc/systemd/system/hub-sentinel.{service,timer}
sudo systemctl daemon-reload
sudo systemctl enable --now hub-sentinel.timer
```

## Manual check

```bash
python3 deploy/hub_sentinel_cli.py --dry-run --hub-url http://hub-a:8321
```

`--dry-run` probes and prints what it would send, but never writes state or
calls a sink.

**Out of scope**: automatic hub failover or restart, and any change to hub
code — this sentinel must never need a hub deploy to work.
