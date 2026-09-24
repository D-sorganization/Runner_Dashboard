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

## Ordered Step-by-Step Checklist

Every step is marked **[Owner]** or **[Agent]**. Execute in sequence from probe to acceptance.

---

### Phase 1: Windows Host & Ollama Network Hardening

Windows Ollama must bind strictly to loopback `127.0.0.1:11434`. The WSL instance connects via the WSL virtual adapter default gateway forwarded through a scoped portproxy.

- [ ] **1.1 [Owner] Remove broad Ollama environment variables.**
  In Windows System Properties → Environment Variables, delete `OLLAMA_HOST` from both User and System variables (or set it strictly to `127.0.0.1:11434`). Restart the Ollama desktop application from the system tray.

- [ ] **1.2 [Owner] Disable exposed Windows firewall rules.**
  Open Windows Defender Firewall with Advanced Security (or elevated PowerShell) and disable/remove non-scoped inbound rules for port 11434, specifically:
  - Inbound rule allowing port 11434 from `Any`
  - Inbound rule allowing port 11434 from `Tailscale`
  - Temporary rule named `TEMP Any`

- [ ] **1.3 [Owner] Install the managed Ollama WSL Bridge.**
  In an elevated **PowerShell as Administrator**, run:
  ```powershell
  powershell.exe -NoProfile -File "deploy\windows\ollama-wsl-bridge.ps1" -Install
  ```
  This creates `%ProgramData%\RunnerDashboard\OllamaWslBridge`, configures scoped portproxy forwarding only on the WSL vEthernet adapter (`192.168.x.1:11434` -> `127.0.0.1:11434`), creates the firewall rule `StaffHub-Ollama-WSL`, and registers the scheduled SYSTEM task `StaffHub-Ollama-WSL-Bridge`.

- [ ] **1.4 [Agent] Verify Windows bridge status.**
  Verify the scheduled task returned exit code 0 and created `%ProgramData%\RunnerDashboard\OllamaWslBridge\result.json`.

---

### Phase 2: WSL Authentication, Runtimes & Provider CLIs

Paths starting with `~` refer to `/home/dieterolson` in the **Ubuntu** WSL distro on ControlTower.

- [ ] **2.1 [Owner] Authenticate WSL GitHub CLI.**
  In a WSL interactive terminal, run:
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
  - **Antigravity**:
    Symlink `~/.local/bin/agy` to the Windows WinGet binary:
    `/mnt/c/Users/diete/AppData/Local/Microsoft/WinGet/Packages/Google.AntigravityCLI_Microsoft.Winget.Source_8wekyb3d8bbwe/agy.exe`
  - **Cursor Agent**:
    Install `cursor-agent` into `~/.local/bin/cursor-agent`.

- [ ] **2.4 [Owner] Authenticate provider subscriptions in WSL.**
  - **Claude Code**: Run `claude auth login` in an interactive shell.
  - **Codex**: Run `codex` and complete ChatGPT seat sign-in.
  - **Antigravity**: Run `agy` and complete Google account sign-in.
  - **Cursor**: Run `cursor-agent` and verify subscription credentials.

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
  Ensure all paths referenced in `ReadWritePaths` exist before reloading systemd.

- [ ] **5.2 [Owner] Enable user lingering.**
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

### Phase 6: Verification & Acceptance

- [ ] **6.1 [Agent] Run the fleet acceptance script.**
  Run:
  ```bash
  deploy/staff-node-acceptance.sh --role worker
  ```
  Verify all checks pass (deployment, sign-ins, CLIs, drop-in, role source, holds, scheduler=0, Ollama reachability).

- [ ] **6.2 [Agent] Verify live rm_source and empty holds.**
  - `curl -fsS http://127.0.0.1:8321/api/staff/board?local=1` reports `rm_source.status == "ok"` and `scheduler: 0`.
  - `curl -fsS http://127.0.0.1:8321/api/staff/schedule` reports no worker holds.

- [ ] **6.3 [Agent] Execute ad-hoc runs for all 6 providers.**
  Execute `deploy/staff-node-acceptance.sh --role worker --run-ad-hoc` or submit ad-hoc test runs for:
  - `claude`
  - `codex`
  - `antigravity`
  - `cursor-agent`
  - `ollama` (via Codex)
  - `claude-ollama`
  Confirm all six reach status `succeeded` with exit code 0 and `STAFF_RESULT: ok`.

- [ ] **6.4 [Owner] Final signoff.**
  ControlTower is verified and admitted as an active Staff Hub worker node.
