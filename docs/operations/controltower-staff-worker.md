# ControlTower Staff Hub Worker Runbook

Governing epic: [#1192](https://github.com/D-sorganization/Runner_Dashboard/issues/1192); node qualification issue: [#1273](https://github.com/D-sorganization/Runner_Dashboard/issues/1273).
ControlTower is the third and final node to join the Staff Hub fleet as a worker; DeskComputer (scheduler) and OGLaptop (worker) are qualified.

## Baseline Probe Findings (2026-09-23 17:00 PT)

The initial probe of ControlTower recorded:
- Dashboard version: `4.10.0` at commit `f510c4c` (pre-dating #1250, #1253, #1256, and #1262).
- WSL GitHub CLI: `/usr/bin/gh` installed but not authenticated.
- Runtimes & CLIs: no Node or Linux provider CLIs (`claude`, `codex`, `agy`, `cursor-agent`) installed in WSL.
- Repositories: no `~/staff-repos` or `staff.gitconfig`.
- Roles source: pointed to frozen `~/staff-bundle/rm/roles`.
- Sync timer & lingering: no `runner-dashboard-rm-sync.timer` installed; systemd user lingering disabled.
- Systemd drop-in: `/etc/systemd/system/runner-dashboard.service.d/staff-hub.conf` missing provider paths and Cursor paths.
- Windows Ollama: exposed to network (`OLLAMA_HOST=0.0.0.0`), with broad inbound firewall rules ("Any", "Tailscale", and a temporary "TEMP Any" rule).

Progress since the probe (2026-09-23 17:10 PT, done from DeskComputer): dashboard redeployed to `5c143ac` (Python 3.12 artifact, see 4.1), user lingering enabled (5.2), and `%h/.cursor %h/.config/cursor` added to the drop-in `ReadWritePaths` (backup `staff-hub.conf.bak-*-cursor`). The acceptance script passed 7 of 22 checks at that point.

Facts: WSL distro **`ControlTower-Runner`** (Python 3.12 only), Linux user `dieterolson`, `NUM_RUNNERS=3`. An agent on another host reaches this WSL only through `_deploy\remote\run_task.py` (S4U scheduled task); never run `wsl.exe` over SSH. Back up every file before changing it (`*.bak-<stamp>`).

## Ordered Step-by-Step Checklist

Every step is marked **[Owner]** or **[Agent]**. Execute in sequence from probe to acceptance.

---

### Phase 1: Windows Host & Ollama Network Hardening

Windows Ollama must bind strictly to loopback `127.0.0.1:11434`. The WSL instance connects via the WSL virtual adapter default gateway forwarded through a scoped portproxy.

- [ ] **1.1 [Owner] Remove broad Ollama environment variables.**
  In Windows System Properties → Environment Variables, delete `OLLAMA_HOST` from both User and System variables (or set it strictly to `127.0.0.1:11434`). On ControlTower it is the **User** variable `OLLAMA_HOST=0.0.0.0:11434`. Also turn off "Expose Ollama to the network" in the Ollama settings, then quit and restart Ollama from the system tray (the setting only takes effect after a restart). Check: `Get-NetTCPConnection -LocalPort 11434 -State Listen` shows only `127.0.0.1`.

- [ ] **1.2 [Owner] Disable exposed Windows firewall rules.**
  Open Windows Defender Firewall with Advanced Security (or elevated PowerShell) and **disable** (do not delete) the non-scoped inbound rules for port 11434. On ControlTower they are named `ollama.exe`, `Ollama Tailscale` and `Ollama TEMP Any`: `Get-NetFirewallRule -DisplayName 'ollama.exe','Ollama Tailscale','Ollama TEMP Any' | Disable-NetFirewallRule`. Re-read them with `Get-NetFirewallRule` to confirm `Enabled: False`; `-PassThru` output can be stale. The rules are:
  - Inbound rule allowing port 11434 from `Any`
  - Inbound rule allowing port 11434 from `Tailscale`
  - Temporary rule named `TEMP Any`

- [ ] **1.3 [Owner] Install the managed Ollama WSL Bridge.**
  In an elevated **PowerShell as Administrator**, run:
  ControlTower has no Windows checkout of this repository; the script is copied to `_deploy`. Run `-DryRun` first and expect `planned`, then `-Install` and expect `configured`:
  ```powershell
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\diete\Repositories\_deploy\windows\ollama-wsl-bridge.ps1" -DryRun
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\diete\Repositories\_deploy\windows\ollama-wsl-bridge.ps1" -Install
  ```
  This creates `%ProgramData%\RunnerDashboard\OllamaWslBridge`, configures scoped portproxy forwarding only on the WSL vEthernet adapter (`192.168.x.1:11434` -> `127.0.0.1:11434`), creates the firewall rule `StaffHub-Ollama-WSL`, and registers the scheduled SYSTEM task `StaffHub-Ollama-WSL-Bridge`.

- [ ] **1.4 [Agent] Verify Windows bridge status.**
  Verify the scheduled task returned exit code 0 and created `%ProgramData%\RunnerDashboard\OllamaWslBridge\result.json`.

---

### Phase 2: WSL Authentication, Runtimes & Provider CLIs

Paths starting with `~` refer to `/home/dieterolson` in the **`ControlTower-Runner`** WSL distro on ControlTower.

- [ ] **2.1 [Owner] Authenticate WSL GitHub CLI.**
  In a ControlTower terminal, open the distro with `wsl -d ControlTower-Runner`, then run:
  ```bash
  gh auth login -h github.com -s admin:org
  ```
  Confirm authentication with `gh auth status` with `GH_TOKEN` and `GITHUB_TOKEN` unset.

- [ ] **2.2 [Agent] Install Node LTS via nvm.**
  Ensure Node v24 LTS (e.g. `v24.21.0`) is installed in WSL:
  ```bash
  export NVM_DIR="$HOME/.nvm"
  [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
  nvm install --lts
  nvm use --lts
  ```
  Ensure nvm initializes properly in `.profile` and `.bashrc` so that non-interactive login shells (`bash -lc`) resolve `node` and `npm`.

- [ ] **2.3 [Agent] Install and link provider CLIs.**
  - **Claude Code**:
    ```bash
    npm install -g @anthropic-ai/claude-code
    ```
  - **Codex**:
    Extract native Linux x64 musl binary from `@openai/codex@0.156.1-linux-x64` into `~/.local/lib/codex-linux-x64/` and symlink to `~/.local/bin/codex`.
    The `codex` already on the WSL `PATH` is a Windows shim; it must not win. Codex 0.9.x fails sign-in with "Missing organization in id_token claims".
  - **Antigravity**:
    Symlink `~/.local/bin/agy` to the Windows WinGet binary:
    `/mnt/c/Users/diete/AppData/Local/Microsoft/WinGet/Packages/Google.AntigravityCLI_Microsoft.Winget.Source_8wekyb3d8bbwe/agy.exe`
  - **Cursor Agent**:
    Install `cursor-agent` into `~/.local/bin/cursor-agent` (`curl -fsS https://cursor.com/install -o /tmp/ci.sh && bash /tmp/ci.sh`).

- [ ] **2.4 [Owner] Authenticate provider subscriptions in WSL.**
  The agent opens each command in a visible window; the owner signs in. The agent never types credentials or codes.
  - **Claude Code**: `CLAUDE_CONFIG_DIR=~/.config/runner-dashboard/claude claude auth login` signs in the service seat directly. A plain `claude auth login` only reaches the service after the copy in 4.3.
  - **Codex**: `codex login --device-auth` (ChatGPT seat).
  - **Antigravity**: `cd ~ && agy`, open the printed URL, sign in with Google, paste the code back, then `/exit`.
  - **Cursor**: `cursor-agent login`.

---

### Phase 3: Isolated Git Config & Repository Clones

- [ ] **3.1 [Agent] Create staff directories.**
  ```bash
  mkdir -p ~/staff-repos ~/staff-worktrees ~/.config/runner-dashboard
  ```

- [ ] **3.2 [Agent] Configure isolated staff git config.**
  Write `~/.config/runner-dashboard/staff.gitconfig`:
  ```ini
  [user]
      name = Dieter Olson
      email = dieterolson@users.noreply.github.com
  [credential "https://github.com"]
      helper = !gh auth token
  [credential "https://gist.github.com"]
      helper = !gh auth token
  [init]
      defaultBranch = main
  ```

- [ ] **3.3 [Agent] Clone required repositories bloblessly under `~/staff-repos`.**
  ```bash
  for repo in Repository_Management Runner_Dashboard Tools Tools_Private UpstreamDrift AffineDrift Gasification_Model; do
      if [ ! -d "$HOME/staff-repos/$repo" ]; then
          git clone --filter=blob:none "https://github.com/D-sorganization/$repo.git" "$HOME/staff-repos/$repo"
      fi
  done
  ```

---

### Phase 4: Dashboard Update & Service Environment

- [ ] **4.1 [Agent] Deploy updated dashboard release artifact.**
  Stop the dashboard service temporarily:
  ```bash
  sudo systemctl stop runner-dashboard
  ```
  Back up the current deployment `~/actions-runners/dashboard` and deploy the packaged 4.10.0 release artifact (built from main, including #1250, #1253, #1256, #1261, #1262).
  Artifacts are Python-ABI specific and this distro has only Python 3.12, so build the artifact with
  `RUNNER_DASHBOARD_PYTHON=<python3.12> bash deploy/package-dashboard-artifact.sh --skip-build --python-minor 3.12`
  and install it with `bash deploy/update-deployed.sh --repo <source checkout> --artifact <tarball>`. Use the same recipe for every later upgrade.

- [ ] **4.2 [Agent] Update service environment file.**
  In `~/.config/runner-dashboard/env`, ensure the following variables are set:
  ```ini
  STAFF_SCHEDULER_ENABLED=0
  STAFF_RM_ROOT=/home/dieterolson/staff-repos/Repository_Management
  STAFF_ROLES_DIR=/home/dieterolson/staff-repos/Repository_Management/staff/roles
  GIT_CONFIG_GLOBAL=/home/dieterolson/.config/runner-dashboard/staff.gitconfig
  CLAUDE_CONFIG_DIR=/home/dieterolson/.config/runner-dashboard/claude
  ```
  *(Important: `STAFF_SCHEDULER_ENABLED` must remain `0` because ControlTower is a worker node; DeskComputer is the fleet scheduler).*

- [ ] **4.3 [Agent] Seed service-owned Claude config.**
  ```bash
  mkdir -p ~/.config/runner-dashboard/claude ~/.config/runner-dashboard/claude-ollama
  if [ -f ~/.claude/.credentials.json ]; then
      cp -p ~/.claude/.credentials.json ~/.config/runner-dashboard/claude/
  fi
  ```

---

### Phase 5: Systemd Drop-in, User Timer & Lingering

- [ ] **5.1 [Agent] Configure service drop-in.**
  In `/etc/systemd/system/runner-dashboard.service.d/staff-hub.conf`:
  ```ini
  [Service]
  MemoryDenyWriteExecute=false
  Environment="PATH=/home/dieterolson/.nvm/versions/node/v24.21.0/bin:/home/dieterolson/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
  ReadWritePaths=%h/.claude %h/.claude.json %h/.codex %h/.gemini %h/.antigravity %h/.cache %h/.local/share
  ReadWritePaths=%h/staff-repos %h/staff-worktrees %h/.config/gh %h/.cursor %h/.config/cursor
  ```
  Ensure all paths referenced in `ReadWritePaths` exist before reloading systemd; a missing one stops the unit from starting.
  The node path in `PATH` must match the installed version (`ls ~/.nvm/versions/node`), not the example above.

- [ ] **5.2 [Owner] Enable user lingering.** Already done on ControlTower (2026-09-23).
  ```bash
  sudo loginctl enable-linger dieterolson
  ```

- [ ] **5.3 [Agent] Install and activate `rm-sync` user timer.**
  ```bash
  mkdir -p ~/.config/systemd/user
  cp deploy/systemd-user/runner-dashboard-rm-sync.* ~/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable --now runner-dashboard-rm-sync.timer
  systemctl --user start runner-dashboard-rm-sync.service
  ```

- [ ] **5.4 [Agent] Reload and start runner-dashboard service.**
  ```bash
  sudo systemctl daemon-reload
  sudo systemctl start runner-dashboard
  ```

---

- [ ] **5.5 [Owner] Reserve ControlTower's LAN address.**
  In the eero app, add a DHCP reservation for ControlTower's LAN interface (see "Node LAN addressing" in `docs/staff-hub.md`). Record the MAC in the private deploy notes, not in this repository.

---

### Phase 6: Verification & Acceptance

- [ ] **6.1 [Agent] Run the fleet acceptance script.**
  Run:
  ```bash
  deploy/staff-node-acceptance.sh --role worker --expect-sha <current main SHA>
  ```
  Verify all checks pass (deployment, sign-ins, CLIs, drop-in, role source, holds, scheduler=0, Ollama reachability).

- [ ] **6.2 [Agent] Verify live rm_source and empty holds.**
  - `curl -fsS http://127.0.0.1:8321/api/staff/board?local=1` reports `rm_source.status` of `updated` or `unchanged`, checked within the last hour.
  - `curl -fsS http://127.0.0.1:8321/api/staff/schedule` reports `"enabled": false`.
  - `curl -fsS http://127.0.0.1:8321/api/staff/schedule` reports no worker holds.

- [ ] **6.3 [Agent] Execute ad-hoc runs for all 6 providers.**
  Execute `deploy/staff-node-acceptance.sh --role worker --run-ad-hoc` or submit ad-hoc test runs for:
  - `claude`
  - `codex`
  - `antigravity`
  - `cursor-agent`
  - `ollama` (via Codex)
  - `claude-ollama`
  Confirm all six reach status `succeeded` with exit code 0 and `STAFF_RESULT: ok`. Each run can take several minutes; the script waits up to 15 minutes per provider.

- [ ] **6.4 [Owner] Final signoff.**
  ControlTower is verified and admitted as an active Staff Hub worker node.
