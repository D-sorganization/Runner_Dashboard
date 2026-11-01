# barb-worker VM (#3658)

Ubuntu 24.04 worker with `barb-grok`, `barb-dots`, and `barb-duet`. Each userspace Tailscale proxy listens on localhost **1046**, **1056**, or **1066** and reaches the Desk hub at `https://deskcomputer.tail2bbcc7.ts.net:8321` (ports **443** / **8321** only).

## Principals

| UNIX user   | Hub principal        | Proxy port |
| ----------- | -------------------- | ---------- |
| `barb-grok` | `agent-grok-vm`      | 1046       |
| `barb-dots` | `agent-codex-hands`  | 1056       |
| `barb-duet` | `agent-duet`         | 1066       |

Bearer tokens live in `~/.config/runner-dashboard/token` (mode **600**). Shell profile and
systemd user units export `FLEET_API_TOKEN` from that file without printing it. See
[barb-lane-principals.md](barb-lane-principals.md) for minting on the hub.

## Bootstrap (vmadmin)

From a `Runner_Dashboard` checkout on the VM (or copy `scripts/barb-worker/` onto the host):

```bash
cd ~/Runner_Dashboard   # or your checkout path
sudo bash scripts/barb-worker/bootstrap.sh
```

Idempotent: safe to re-run after hub token installs or repo updates.

Optional:

- `BARB_CLONE_ALL_FLEET_REPOS=1` — clone every repo listed in `config/projects.json` (default is the core sibling set).
- `BARB_WORKER_HUB_URL` — override hub base URL (default Desk URL above).

### Install hub tokens (owner; never log or paste)

Mint each principal on the hub (admin session), then on **barb-worker** as root:

```bash
install -d -m 700 ~barb-grok/.config/runner-dashboard
install -m 600 /dev/stdin ~barb-grok/.config/runner-dashboard/token <<<"$TOKEN"
# repeat for barb-dots and barb-duet
```

Restart lane services after tokens are in place:

```bash
sudo -u barb-grok  systemctl --user restart barb-lane.service
sudo -u barb-dots  systemctl --user restart barb-lane.service
sudo -u barb-duet  systemctl --user restart barb-lane.service
```

### Verify

```bash
sudo bash scripts/barb-worker/bootstrap.sh verify
```

Prints one line per user with `livez=pass|fail` and `staff_summary=pass|fail` only (no tokens).

## What gets installed

Per user:

- `HTTP_PROXY` / `HTTPS_PROXY` → that user's localhost proxy port (shell + `~/.config/systemd/user/barb-fleet.env`)
- `~/.config/runner-dashboard/` (mode **700**) and `token` file (**600**)
- Fleet git checkouts under `~/src/` (at minimum `Runner_Dashboard` and `Repository_Management`)
- User systemd unit **`barb-lane.service`** running `scripts/barb-worker/lane-loop.sh` (rd-threads inbox poll via `barb_cli`)

Shared on the VM (once):

- `nodejs`, `npm`, `git`, `gh`, `python3`, `uv`
- Global `@anthropic-ai/claude-code` and `@openai/codex` CLIs

## GitHub CLI on spokes

Hub-minted App tokens for spoke `gh`/`git` (#2051) are wired through a hook:
`scripts/barb-worker/install-hub-fleet-gh.sh`. When that helper is absent on `main`, bootstrap
leaves the system `gh` on `PATH` and documents manual setup; do not fall back to personal
`ghp_` tokens on lane users.

## Lane loop

`lane-loop.sh` sources `with-fleet-env.sh`, then polls `python3 scripts/barb_cli.py inbox` on an
interval (`BARB_LANE_INTERVAL_S`, default 30). This reuses the rd-threads client in
`scripts/barb_cli.py` (#3072). Hub-side postman keeper logic (`scripts/rd_barb_postman.py`) stays
on the hub; VM lanes only maintain inbox presence.

## Related

- [Barb lane principals](barb-lane-principals.md)
- [Codex hands (Dots)](codex-hands.md)
- [Assistant bridge README](README.md)
