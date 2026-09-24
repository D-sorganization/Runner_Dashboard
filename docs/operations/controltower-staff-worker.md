# ControlTower Staff Hub worker

ControlTower is the fleet hub (`HUB_URL`) and the last node to become a Staff
Hub worker (#1273, epic #1192). DeskComputer schedules and OGLaptop is a
worker. Both are set up and pass `deploy/staff-node-acceptance.sh`. This page
is the ordered path for ControlTower. Every step names who does it:

- **Owner** steps are sign-ins, Windows security settings and router
  settings. Only Dieter does these.
- **Agent** steps are run by an agent session on ControlTower, or by an agent
  on another host through `_deploy\remote\run_task.py` (S4U task). Never run
  `wsl.exe` over SSH.

Facts: WSL distro `ControlTower-Runner` (Python 3.12 only), Linux user
`dieterolson`, dashboard service `runner-dashboard`, `NUM_RUNNERS=3` by policy.
The **Staff scheduler stays off** here (`STAFF_SCHEDULER_ENABLED=0`).
Back up every file before you change it (`*.bak-<stamp>`). Disable firewall
rules; never delete them.

## State

| #   | Item                                                                 | 2026-09-23 17:10 PT                                                                                  |
| --- | -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| 1   | Dashboard at current `main` (Python 3.12 artifact)                   | ✅ `5c143ac`                                                                                         |
| 2   | User lingering (the `rm-sync` user timer needs it)                   | ✅ enabled                                                                                           |
| 3   | Service drop-in `ReadWritePaths` for `~/.cursor`, `~/.config/cursor` | ✅ added (backup `staff-hub.conf.bak-*-cursor`)                                                      |
| 4   | WSL `gh` signed in                                                   | ❌ owner                                                                                             |
| 5   | node + Linux `claude`, `codex` ≥ 0.156, `agy`, `cursor-agent`        | ❌ none; the `codex` on PATH is a Windows shim                                                       |
| 6   | Provider sign-ins                                                    | ❌ owner                                                                                             |
| 7   | `staff.gitconfig`, `~/staff-repos` (7 blobless clones)               | ❌                                                                                                   |
| 8   | Role source = live RM clone + `runner-dashboard-rm-sync` timer       | ❌ still the frozen `~/staff-bundle/rm`                                                              |
| 9   | Provider CLIs on the service `PATH`                                  | ❌                                                                                                   |
| 10  | Windows Ollama loopback-only behind the WSL bridge                   | ❌ `OLLAMA_HOST=0.0.0.0`; inbound Allow rules for Any and Tailscale, and one named `Ollama TEMP Any` |
| 11  | DHCP reservation on the router                                       | ❌ owner                                                                                             |
| 12  | `staff-node-acceptance.sh --scheduler off --dispatch` passes         | ❌                                                                                                   |

Steps 1–3 were done from DeskComputer: a Python 3.12 artifact built with
`RUNNER_DASHBOARD_PYTHON=<3.12 venv>` and `package-dashboard-artifact.sh
--python-minor 3.12`, shipped with `git archive` source through `run_task.py`,
and installed with `deploy/update-deployed.sh --artifact`. Repeat that recipe
for later upgrades until the node's own clone can build.

## Steps

1. **Owner:** in a ControlTower terminal, run
   `wsl -d ControlTower-Runner -- bash -lc "gh auth login"` (GitHub.com, HTTPS,
   browser or device code).
   **Agent** check: `env -u GH_TOKEN -u GITHUB_TOKEN gh auth status`.
2. **Agent:** install the CLIs in WSL.
   - Node LTS for `dieterolson`: nvm, or NodeSource if `sudo -n true` works.
   - `npm i -g @anthropic-ai/claude-code`.
   - Codex 0.156+ as the Linux binary: `npm pack @openai/codex@<ver>-linux-x64`,
     extract it into `~/.local/lib/codex-linux-x64`, and make `~/.local/bin/codex`
     exec `package/vendor/x86_64-unknown-linux-musl/bin/codex`. Codex 0.9.x fails
     sign-in with "Missing organization in id_token claims".
   - agy: if Windows has the WinGet `Google.AntigravityCLI` package, symlink
     `~/.local/bin/agy` to its `agy.exe`. Otherwise ask the owner to install it.
   - Cursor CLI: `curl -fsS https://cursor.com/install -o /tmp/ci.sh && bash /tmp/ci.sh`.
   - Check: `command -v node claude codex agy cursor-agent` in a login shell.
3. **Owner** sign-ins, each in a visible window the agent opens. The agent
   never types credentials or codes.
   - `codex login --device-auth`
   - `cd ~ && agy`, then open the URL it prints, sign in with Google, paste
     the code back and `/exit`
   - `CLAUDE_CONFIG_DIR=~/.config/runner-dashboard/claude claude auth login`.
     A plain `claude auth login` does not reach the service.
   - `cursor-agent login`
4. **Agent:** staff git and clones.
   - Copy `C:\Users\diete\Repositories\_deploy\staff.gitconfig` to
     `~/.config/runner-dashboard/staff.gitconfig`. It holds no token; it uses the
     `gh auth token` credential helper.
   - With `GIT_CONFIG_GLOBAL` pointing at that file, make blobless clones of
     AffineDrift, Gasification_Model, Repository_Management, Runner_Dashboard,
     Tools, Tools_Private and UpstreamDrift into `~/staff-repos`.
5. **Agent:** switch the role source (`docs/staff-hub.md`, "Live Repository
   Management source").
   - Back up `~/.config/runner-dashboard/env` and `staff_holds.json`.
   - In the env file, set `STAFF_RM_ROOT=/home/dieterolson/staff-repos/Repository_Management`,
     `STAFF_ROLES_DIR=$STAFF_RM_ROOT/staff/roles` and `GIT_CONFIG_GLOBAL=...`.
     Leave `STAFF_SCHEDULER_ENABLED=0`.
   - Install `deploy/systemd-user/runner-dashboard-rm-sync.{service,timer}` into
     `~/.config/systemd/user`, then run `systemctl --user daemon-reload` and
     `systemctl --user enable --now runner-dashboard-rm-sync.timer`.
   - Keep `~/staff-bundle` for rollback.
6. **Agent:** service `PATH`. In
   `/etc/systemd/system/runner-dashboard.service.d/staff-hub.conf` (back it up
   first), add
   `Environment="PATH=<nvm node bin>:/home/dieterolson/.local/bin:/usr/lib/wsl/lib:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"`.
   Then run `sudo systemctl daemon-reload && sudo systemctl restart runner-dashboard`.
   After the restart, `GET /api/staff/schedule` must show no held worker roles.
7. **Owner:** Ollama, following "Ollama for WSL" in `docs/staff-hub.md`.
   Nothing in either repository or in the other nodes' configuration uses
   ControlTower's tailnet-exposed Ollama (checked 2026-09-23), so it follows the
   loopback standard:

   1. Remove the **User** variable `OLLAMA_HOST=0.0.0.0:11434`, turn off "Expose
      Ollama to the network", then quit and restart Ollama. Check with
      `Get-NetTCPConnection -LocalPort 11434 -State Listen`: only `127.0.0.1`.
   2. In an elevated PowerShell, **disable** (not delete) the inbound rules
      `ollama.exe`, `Ollama Tailscale` and `Ollama TEMP Any`:
      `Get-NetFirewallRule -DisplayName 'ollama.exe','Ollama Tailscale','Ollama TEMP Any' | Disable-NetFirewallRule`.
      Check `Enabled: False` with `Get-NetFirewallRule -DisplayName ...`; the
      `-PassThru` output can be stale.
   3. Run `powershell -ExecutionPolicy Bypass -File C:\Users\diete\Repositories\_deploy\windows\ollama-wsl-bridge.ps1 -DryRun`
      and expect `planned`. Then run it again with `-Install` and expect
      `configured`.

   If the tailnet Ollama is ever wanted again, that is a separate design
   decision. Do not re-open `Any`.

8. **Owner:** in the eero app, add a DHCP reservation for ControlTower's LAN
   interface (see "Node LAN addressing" in `docs/staff-hub.md`). Record the MAC
   in the private deploy notes, not here.
9. **Agent:** acceptance on ControlTower:
   `bash ~/staff-repos/Runner_Dashboard/deploy/staff-node-acceptance.sh --scheduler off --dispatch`
   (or the copy in `/mnt/c/Users/diete/Repositories/_deploy/`). Every line must
   be `PASS`. Then run the fleet acceptance in `docs/staff-hub.md`.

## Recovery Notes

- The dashboard does not come back after a restart: `journalctl -u
runner-dashboard -n 80`. A `ReadWritePaths` entry whose directory is missing
  stops the unit from starting; create the directory.
- `rm_source` shows `skipped`: the RM clone is dirty or not on `main`. Inspect
  it; never reset it automatically.
- Codex or agy runs fail while the CLI works in a shell: compare
  `systemctl show runner-dashboard -p Environment` with `command -v <cli>`. The
  service `PATH` is missing the CLI's directory.
- No network after a Windows reboot: see "Node LAN addressing" in
  `docs/staff-hub.md` (duplicate DHCP address, event 4199). Do not turn firewall
  profiles off.
