# barb-worker VM (#3658)

Ubuntu 24.04 worker with `barb-grok`, `barb-dots`, and `barb-duet`. Each userspace Tailscale proxy listens on localhost **1046**, **1056**, or **1066** and reaches the fleet hub over HTTPS through that proxy (Tailscale Serve on the hub; no direct `:8321` URL).

## Principals

| UNIX user   | Hub principal        | Proxy port |
| ----------- | -------------------- | ---------- |
| `barb-grok` | `agent-grok-vm`      | 1046       |
| `barb-dots` | `agent-codex-hands`  | 1056       |
| `barb-duet` | `agent-duet`         | 1066       |

Bearer tokens live in `~/.config/runner-dashboard/token` (mode **600**). Shell profile and systemd user units export `FLEET_API_TOKEN` from that file without printing it. See [barb-lane-principals.md](barb-lane-principals.md) for minting on the hub.

## Hub URL (operator)

Tracked defaults use the documented example origin `https://hub-a.fleet-example.ts.net` (no port). On the live barb-worker VM, set the Desk hub HTTPS origin before bootstrap:

```bash
export BARB_WORKER_HUB_URL='https://<desk-hub>.ts.net'   # owner: Tailscale Serve HTTPS only
```

That value is written into each lane’s shell and systemd env as `FLEET_API_URL`.

## Bootstrap (vmadmin)

Bootstrap clones **`Runner_Dashboard` once** to **`/opt/Runner_Dashboard`** (world-readable) so every lane user and the hub-minted `gh` wrapper can use the same tree. Override with `BARB_RUNNER_DASHBOARD_ROOT` when testing.

Run install from any checkout of this repo on the VM (the script does not rely on lane users having their own `Runner_Dashboard` under `~/src`):

```bash
export BARB_WORKER_HUB_URL='https://<desk-hub>.ts.net'
cd ~/src/Runner_Dashboard   # or another admin checkout
sudo -E bash scripts/barb-worker/bootstrap.sh
```

Idempotent: safe to re-run after hub token installs or repo updates.

Optional:

- `BARB_CLONE_ALL_FLEET_REPOS=1` — clone every repo listed in `config/projects.json` under each lane user’s `~/src/` (default is the core sibling set except `Runner_Dashboard`, which stays under `/opt`).

### Install hub tokens (owner; never log or paste)

Mint each principal on the hub (admin session), then on **barb-worker** as root:

```bash
install -d -m 700 ~barb-grok/.config/runner-dashboard
install -m 600 /dev/stdin ~barb-grok/.config/runner-dashboard/token <<<"$TOKEN"
# repeat for barb-dots and barb-duet
```

Restart lane services after tokens are in place (`loginctl enable-linger` is applied during bootstrap so user systemd works without an interactive login):

```bash
for u in barb-grok barb-dots barb-duet; do
  sudo -u "$u" env XDG_RUNTIME_DIR="/run/user/$(id -u "$u")" systemctl --user restart barb-lane.service
done
```

### Verify

```bash
sudo -E bash scripts/barb-worker/bootstrap.sh verify
```

Prints one line per user with `livez=pass|fail` and `staff_summary=pass|fail` only (no tokens).

## What gets installed

Per user:

- `HTTP_PROXY` / `HTTPS_PROXY` → that user's localhost proxy port (shell + `~/.config/systemd/user/barb-fleet.env`)
- `~/.config/runner-dashboard/` (mode **700**) and `token` file (**600**)
- `RUNNER_DASHBOARD_ROOT=/opt/Runner_Dashboard` in shell and systemd env
- Other fleet git checkouts under `~/src/` (for example `Repository_Management`; not a second `Runner_Dashboard`)
- **`loginctl enable-linger`** so user systemd units run without a graphical session
- User systemd unit **`barb-lane.service`** running `/opt/Runner_Dashboard/scripts/barb-worker/lane-loop.sh` (rd-threads inbox poll via `barb_cli`)

Shared on the VM (once):

- **`/opt/Runner_Dashboard`** — shared hub checkout for lane loops and hub-minted GitHub CLI
- `git`, `gh`, `python3`, `uv`
- **Node.js ≥ 22** (NodeSource when Ubuntu’s default Node is older) and global `@anthropic-ai/claude-code` and `@openai/codex` CLIs

## GitHub CLI on spokes

Hub-minted App tokens for spoke `gh`/`git` (#2051) are wired through a hook:
`scripts/barb-worker/install-hub-fleet-gh.sh`. Bootstrap runs it **after** `/opt/Runner_Dashboard` exists (the helper needs `scripts/barb_github_hub_token.py` from that tree). When the helper is absent on `main`, bootstrap
leaves the system `gh` on `PATH` and documents manual setup; do not fall back to personal
`ghp_` tokens on lane users.

## Lane loop

`lane-loop.sh` sources `with-fleet-env.sh`, then polls `python3 scripts/barb_cli.py inbox` on an
interval (`BARB_LANE_INTERVAL_S`, default 30). Hub-side postman keeper logic (`scripts/rd_barb_postman.py`) stays on the hub; VM lanes only maintain inbox presence.

## Panel Relay

`scripts/barb_panel_relay.py` starts hub expert panels for agents that file `panel-request` issues in barbara ([format](panel-requests.md)). It is **off by default** and runs only on the `barb-duet` lane. Bootstrap does not install it.

To enable it, as `barb-duet` add the settings to `~/.config/systemd/user/barb-fleet.env`:

```bash
PANEL_RELAY_ENABLED=1
# optional: PANEL_RELAY_REPO, PANEL_RELAY_DAILY_CAP (default 3, resets 00:00 UTC),
# PANEL_RELAY_AUTHORS (comma list; replaces the fleet-bot defaults, blank keeps them)
```

Then add a user timer that runs one pass every five minutes:

```ini
# ~/.config/systemd/user/barb-panel-relay.service
[Service]
Type=oneshot
EnvironmentFile=%h/.config/systemd/user/barb-fleet.env
ExecStart=/bin/bash -c 'source /opt/Runner_Dashboard/scripts/barb-worker/with-fleet-env.sh && exec python3 /opt/Runner_Dashboard/scripts/barb_panel_relay.py'

# ~/.config/systemd/user/barb-panel-relay.timer
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

Before enabling it, check one pass with `python3 scripts/barb_panel_relay.py --dry-run`. That reads barbara and the hub's presets and writes nothing. State, meaning per-issue outcomes and daily starts, lives in `~/.local/state/barb-panel-relay.json`. If that file is unreadable the relay exits with status 2 and starts nothing; fix it or move it aside. To switch it off, unset `PANEL_RELAY_ENABLED` or stop the timer.

## Related

- [Barb lane principals](barb-lane-principals.md)
- [Codex hands (Dots)](codex-hands.md)
- [Panel requests through barbara](panel-requests.md)
- [Assistant bridge README](README.md)
- [Fleet Python client](../../clients/fleet/README.md#barb-worker-vm)
