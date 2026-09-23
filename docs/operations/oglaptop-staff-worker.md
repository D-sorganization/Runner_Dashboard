# OGLaptop Staff Hub worker

Last verified: **2026-09-23**, on OGLaptop itself. Governing epic: #1192;
node setup: #1223; deployed provider changes: PRs #1250, #1253 and #1256;
reusable node standards: #1257 / #1258 (merged PRs #1261 / #1262).
This is a verified snapshot, not a claim about other machines or future uptime.

## Current status

OGLaptop is configured as a worker. **Keep `STAFF_SCHEDULER_ENABLED=0`.**
This setup did not change DeskComputer or ControlTower or enable a second scheduler.

| Item | Verified value |
| --- | --- |
| Windows host / WSL distro | `OGLaptop` / `Ubuntu` |
| Linux user / home | `dieterolson` / `/home/dieterolson` |
| Dashboard service | `runner-dashboard`, active; loopback port `8321` |
| Dashboard version / deployed commit | `4.10.0` / `35686c4ebb3c6b65596a43ed6028fc535fea1b27` |
| Required changes | Includes #1250, #1253, #1256, #1261 and #1262; built from merged main |
| Runtime | uv-managed CPython `3.11.15`; artifact wheel ABI `3.11` |
| Scheduler | `0`, verified in the running process environment after deployment |
| Final board | All six requested providers available; zero running or queued staff jobs |

The version string alone cannot distinguish the old `f510c4c` deployment from
this deployment. Check `deployment.git_sha` in `GET /api/health`.

## Controlled restart maintenance (2026-09-23)

The owner authorized a CI drain before restart validation. WSL GitHub login
needed an owner-operated `gh auth refresh -h github.com -s admin:org` first.
Runner IDs 217–224 belong to this node (GitHub spells its name `Oglaptop`,
while local systemd units use `OGLaptop`). Their original group is ID 1.

Do not assume the existing `Bandwidth-Draining` group denies jobs: group 5
currently permits four repositories and contains another host's runners.
This maintenance uses `OGLaptop-Maintenance-20260923` (ID 6), visibility
`selected`, zero selected repositories and no public repository access.
Only OGLaptop's eight runners were moved there; active jobs were allowed to
finish. Do not invoke the runner units' force-drain stop hook while busy.

Maintenance records are under
`C:\Users\diete\Repositories\_deploy\oglaptop-drain-20260923`:
original runner IDs/group membership, enabled/active unit states, keepalive
task XML backup, pre-restart boot ID, and completion evidence. The CI
`runner-scheduler.timer` and runner auto-starts were temporarily disabled;
the independent Staff Hub scheduler remains off throughout.

Restore group membership to ID 1 after validation, restore the original
enabled states (units 1–4 enabled, 5–8 disabled), and re-enable the existing
CI scheduler timer. Let the governed scheduler select active capacity;
do not start all eight runners. Retain the empty maintenance group and
backups. Windows reboot and remote port-isolation acceptance require their
own observed results; a successful WSL restart alone does not prove them.

At **14:53:55 PT**, GitHub reported all eight runners idle and no local
`Runner.Worker` remained. All listeners were stopped before `wsl --shutdown`.
Only Ubuntu was running. The Windows keepalive task was paused, its drain
marker archived afterward, and the task resumed after startup. Boot ID changed
from `5e9c7e7b-5ed0-4dd3-aa5a-16c513582428` to
`98d96adc-ed37-4677-a3a4-a6c0cf2249b6`.

The dashboard started automatically at the same deployed commit. Gateway
`192.168.208.1:11434` returned Ollama `0.34.2` without a bridge repair;
`/run/WSL/1_interop` again resolved to the live `2_interop` socket. Scheduler
remained `0`, all six provider flags were true, and worker holds were empty.
Post-restart health runs all succeeded with exit 0:

| Provider | Run |
| --- | --- |
| Claude | `run-a51cc1408152` |
| Codex | `run-318d9b6b39aa` |
| Antigravity | `run-77a4d6df013f` |
| Cursor | `run-058dbc8b19c5` |
| Ollama via Codex | `run-80a6f535fa67` |
| Ollama via Claude | `run-d8707eb93210` |

WSLg mounted another tmpfs over `/run/user/1000`, hiding the user bus socket
from `systemctl --user`. This was a control-socket problem: the system journal
proves `runner-dashboard-rm-sync.timer` started at 14:54:31 and its service ran
successfully at 14:56:35. After provider checks finished,
`sudo systemctl restart user@1000.service` restored control-socket access and
the timer was confirmed active. No permanent unit or WSLg setting was changed.
If this recurs, inspect the system journal before concluding the timer stopped;
restarting a user manager interrupts its user services and requires a quiet
maintenance window.

## Windows reboot and network diagnosis (2026-09-23)

Windows boot time changed to **15:05:09 PT**. The bridge wrote a successful
configured result at **15:06:35**, and WSL could reach Windows Ollama afterward.
The dashboard recovered at the same deployed commit, with Staff scheduling
off, empty worker holds and all six providers available. All post-Windows-reboot
checks succeeded with exit 0:

| Provider | Run |
| --- | --- |
| Claude | `run-864e41672c0b` |
| Codex | `run-ecbdbcfcd9f7` |
| Antigravity | `run-0ddcad1824a4` |
| Cursor | `run-ecc6678e11bb` |
| Ollama via Codex | `run-342a2641c92c` |
| Ollama via Claude | `run-b04b13787763` |

The owner reported all Windows websites unavailable after reboot. System/Tcpip
event **4199** recorded duplicate address `192.168.4.202` at **15:05:46,
15:11:00 and 15:18:03**, conflicting with MAC `90-6A-EB-81-45-30` (not a local
adapter). DNS also timed out. Wi-Fi is DHCP-managed by `192.168.4.1`; after
reconnection it acquired `192.168.4.203` at **15:21:24**. The duplicate address
is the strongest observed explanation for the outage. Prevent recurrence by
identifying that device and correcting router DHCP/static-address allocation;
do not assign this laptop an arbitrary static address.

The owner disabled Domain firewall at 15:21:11, shortly before reconnecting.
Private protection remained enabled on the active Wi-Fi profile. A controlled
administrator check subsequently passed GitHub/Google/Cloudflare DNS+HTTPS
with **all three profiles enabled**, demonstrating that disabling Domain was
unnecessary for current connectivity. The bridge only allows inbound TCP11434
from the WSL subnet and does not change profile defaults or outbound policy.

Diagnostic caveat: the first probe rolled back because microsoft.com timed out
even with Domain off. Its delayed rollback then erroneously ran after the
successful second probe, turning Domain off again at 15:29:56. This was a
diagnostic-script error, not the original outage. The scripts were backed up
and corrected to disarm delayed rollback after either a completed rollback
or success. The owner then re-enabled Domain protection in Windows Security.
Final live checks confirmed **Domain, Private and Public all enabled**; Windows
HTTPS returned 200/204/200 for GitHub/Google/Cloudflare, WSL GitHub HTTPS
returned 200, and the WSL Ollama bridge returned version 0.34.2. Wi-Fi retained
DHCP address 192.168.4.203. CI workers were active again after capacity restoration.

CI capacity was restored at approximately **15:33 PT**: runner IDs 217–224
returned to group 1, original boot-enabled states were restored, and the CI
scheduler timer resumed. The governed scheduler started units 1–4; units 5–8
were not manually started. The empty maintenance group and all backups remain.
The separate Staff Hub scheduler is still off. Windows repository work may
resume; coordinate any future runner restarts around active jobs.

Full firewall/profile backups and probe logs:
`C:\Users\diete\Repositories\_deploy\firewall-diagnosis-20260923-152654`
and `firewall-diagnosis-20260923-152938`. Drain and reboot records remain in
`_deploy\oglaptop-drain-20260923`. External port isolation remains unverified.

## Installed tools and authentication

All sign-ins were completed by the owner. Do not inspect, copy, print, or put
credentials into documentation. Future sign-ins must also be owner-operated.

| Tool | Installation / verification |
| --- | --- |
| GitHub CLI | `/usr/bin/gh`; WSL authentication verified with token environment variables unset |
| Node | nvm LTS `v24.21.0`, under `~/.nvm/versions/node/v24.21.0/bin` |
| Claude Code | Linux npm install `2.1.280`; dashboard-config prompt returned `OK` |
| Codex | Native Linux `0.156.1`; ChatGPT sign-in and prompt verified |
| Antigravity | `~/.local/bin/agy` symlinks to the installed Windows WinGet `agy.exe`; Google sign-in and prompt verified |
| Cursor Agent | `2026.09.18-9a7762b`, under `~/.local/bin`; subscription login and model listing verified |
| Ollama | Existing Windows app `0.34.2`; existing models retained; no models downloaded |

Codex's wrapper executes
`~/.local/lib/codex-linux-x64/package/vendor/x86_64-unknown-linux-musl/bin/codex`,
extracted from `@openai/codex@0.156.1-linux-x64`. Do not substitute the old
Windows npm shim: it does not provide the required Linux executable.

Antigravity's Windows target is
`C:\Users\diete\AppData\Local\Microsoft\WinGet\Packages\Google.AntigravityCLI_Microsoft.Winget.Source_8wekyb3d8bbwe\agy.exe`.
Use `agy`, not the legacy Gemini CLI. Gemini is not configured by this setup.

nvm initializes in `.bashrc` for interactive shells and `.profile` for login
shells. Its initialization must also work in a noninteractive `bash -lc`;
the usual early return in `.bashrc` otherwise hides Node and selects Windows shims.

Cursor lists these Grok model families:

- `cursor-grok-4.5-high` and `cursor-grok-4.5-high-fast`.
- `cursor-grok-4.6-{low,medium,high,xhigh}`, each with a `-fast` variant.
- `grok-4.7-{low,medium,high,xhigh}`, each with a `-fast` variant.

## Configuration locations

Paths beginning with `~` below refer to `/home/dieterolson` in **Ubuntu WSL**.

| Setting / resource | Location or value |
| --- | --- |
| Live application | `~/actions-runners/dashboard` |
| Service environment | `~/.config/runner-dashboard/env` |
| Service drop-in | `/etc/systemd/system/runner-dashboard.service.d/staff-hub.conf` |
| Claude seat config | `CLAUDE_CONFIG_DIR=~/.config/runner-dashboard/claude` |
| Claude on Ollama config | `~/.config/runner-dashboard/claude-ollama`; isolated from the Claude seat |
| Staff git config | `GIT_CONFIG_GLOBAL=~/.config/runner-dashboard/staff.gitconfig` |
| Linux repository root | `STAFF_REPOS_ROOT=~/staff-repos` |
| Staff worktree root | `STAFF_WORKTREES_ROOT=~/staff-worktrees` |
| Live RM clone | `STAFF_RM_ROOT=/home/dieterolson/staff-repos/Repository_Management` |
| Live role source | `STAFF_ROLES_DIR=/home/dieterolson/staff-repos/Repository_Management/staff/roles` |
| Windows setup artifacts | `C:\Users\diete\Repositories\_deploy` |

The old `~/staff-bundle/rm` directory is retained for rollback only. The active
user timer `runner-dashboard-rm-sync.timer` refreshes the Linux clone at most
every 15 minutes. User lingering is enabled. The first invocation fast-forwarded
`3057824ebf6ad9d974ebde5514b6e386d4d0ac93` to
`a59cb194fe9a04c8ecc655c112539356a268a45d`, preserving the prior tree as
`refs/staff-rm-backups/bak-2026-09-23-204230-755749`.
`/api/staff/board?local=1` reports revision and freshness in `rm_source`.
`/api/staff/roles` returned 16 roles, and `/api/staff/schedule` had no worker holds.
The timer subsequently picked up merged RM PR #1719 at
`5494676e42dc80b69ffd729d7a98b29dcf4d1100` without a manual pull. The local schema
now includes `claude-ollama`; the roles endpoint still returns 16 roles and
worker holds remain empty.

## Follow-up rollout: #1256 / #1257 / #1258

| Step | Status / evidence |
| --- | --- |
| Documentation | Initial setup report merged as #1259; this runbook is the node status source. |
| Ollama lease fix #1256 | Deployed in `35686c4`; both Ollama harness checks below succeeded. |
| Bridge implementation #1257 | #1261 merged; 15 PowerShell planner tests and real Windows 5.1 dry-run passed. |
| Windows bridge installation | Owner reinstalled the #1265 fix; SYSTEM task returned **0 at 14:24:13 PT**, next run 14:25:12. Installed script hash matches tested source. Scoped forwarding remains working. |
| Live RM source #1258 | #1262 merged and deployed; env migrated, timer active, lingering enabled, first automatic fast-forward verified, worker holds empty. |
| Reboot acceptance | **Pending safe restart window, WSL restart and Windows reboot**; three active CI worker processes were detected after task verification. External tailnet port isolation also remains unverified. |

The owner runs this exact command in **PowerShell as Administrator**:

```powershell
powershell.exe -NoProfile -File "C:\Users\diete\Repositories\_deploy\ollama-wsl-bridge-policy-fix.ps1" -Install
```

The correction selects `RemoteSigned` only for the SYSTEM task's PowerShell process;
it does not change machine-wide or user execution policy. The installed script is
in the administrator-protected ProgramData directory. The original user-context
dry-run succeeded but did not establish that SYSTEM could execute it.

Then verify task `StaffHub-Ollama-WSL-Bridge`, its last result (must be 0), and
`C:\ProgramData\RunnerDashboard\OllamaWslBridge\result.json`. Coordinate WSL
shutdown and reboot with the owner when no staff or CI jobs are active. Follow
[Ollama for WSL](../staff-hub.md#ollama-for-wsl-1257) for repeat health and
external-connectivity checks. Do not claim these acceptance steps from dry-run tests.

After deployment and RM migration, these runs succeeded with exit code 0:

| Provider | Run |
| --- | --- |
| `ollama` (Codex) | `run-0b132c0615f3` |
| `claude-ollama` | `run-1e1e8264a027` |

Release artifact: `~/staff-bundle/rd-rm-1258-py311/dashboard-4.10.0.tar.gz`;
SHA-256 `f0aa4d351326d6c209a85345064154ca598612d711a00a8720062ec1cbe119c1`.
Production frontend build, 37-wheel ABI validation, installer preflight and
offline dependency check passed. Combined local regressions: **62 passed**;
updater mypy, full backend/client Ruff and systemd user-unit validation passed.
The architecture map itself validates; its existing standalone CI pytest job
fails on a missing asyncio plugin option. Required PR checks passed and normal
protected merges completed without an override.

Rollout backups (preserved):

- `~/actions-runners/dashboard.bak-2026-09-23-134138` (complete previous deployment).
- `~/.config/runner-dashboard/env.bak-2026-09-23-134229`.
- `~/.config/runner-dashboard/staff_holds.json.bak-2026-09-23-134229` (holds unchanged).
- RM Git backup ref above; subsequent refreshes retain timestamped status backups.
- Windows `_deploy/STAFF_NODE_LOGIN_PROMPT.md.bak-2026-09-23-1344`; prompt now documents the live clone/timer standard.
- Windows `_deploy/rollout-docs.bak-20260923-1344` and earlier `docs-backups-2026-09-23-124041`, `bridge-docs-backups-20260923`, `rm-docs.bak-20260923-1333` preserve edited documentation.

For role-source rollback, stop/disable the user timer, back up current env, restore
the saved env and restart the dashboard; keep scheduler disabled. Before a full
application rollback, preserve the current deployment and restore the complete
backup above. Do not delete the new deployment or either RM source.

The Windows automation GitHub credential expired during rollout. WSL `gh` remains
authenticated; continued publishing uses Linux Git with the isolated staff config
and `GH_TOKEN` / `GITHUB_TOKEN` unset. Never copy credentials between environments.

The isolated git config contains identity and a GitHub CLI credential helper,
not an embedded token. It avoids CI-written credential rewrites in `~/.gitconfig`.
These seven repositories are cloned under `~/staff-repos`, each with
`remote.origin.partialclonefilter=blob:none`:
AffineDrift, Gasification_Model, Repository_Management, Runner_Dashboard, Tools,
Tools_Private, and UpstreamDrift.

The service PATH starts with
`/home/dieterolson/.nvm/versions/node/v24.21.0/bin:/home/dieterolson/.local/bin`.
It also retains `/usr/lib/wsl/lib`, the user's Cargo bin, and standard Linux bin
directories. Update the service PATH after changing the installed Node version;
shell startup files do not configure systemd's PATH.

The drop-in sets `MemoryDenyWriteExecute=false` and permits writes to `.claude`,
`.claude.json`, `.codex`, `.gemini`, `.antigravity`, `.cache`, `.local/share`,
`.config/gh`, `staff-repos`, `staff-worktrees`, `.cursor`, and `.config/cursor`
under the Linux home. The base unit also permits the runner-dashboard config
directory. Create directory targets before restarting the service.

## Ollama connectivity

The Windows Ollama server remains bound to `127.0.0.1:11434`. A persistent
Windows `portproxy` forwards **only** `192.168.208.1:11434` on the WSL virtual
adapter to that localhost listener. Firewall rule `StaffHub-Ollama-WSL` allows
inbound TCP 11434 only on this local address and WSL interface, from
`192.168.208.0/20`. Existing forwards and rules were preserved.

WSL's gateway at verification was `192.168.208.1`, on
`vEthernet (WSL (Hyper-V firewall))`. From WSL,
`GET http://192.168.208.1:11434/api/version` returned `0.34.2`.
Both Ollama-backed dashboard checks used the adapter default
`glm-5.3-flash:cloud` and succeeded.

`backend/staff/ollama_env.py` discovers the gateway when localhost is unavailable;
no `STAFF_OLLAMA_URL` override was needed. The forwarding and firewall settings
persist, but if Windows reassigns the WSL NAT subnet, update both scoped settings
to the new address/subnet before rerunning health checks. Do not broaden the
listener to all network interfaces as a troubleshooting shortcut.

The local administrator setup script is
`C:\Users\diete\Repositories\_deploy\configure-ollama-wsl-network.ps1`.
It validates the inspected address and refuses to replace existing entries;
it is not a general subnet migration script.

## Initial deployment history and rollback

The source was built without application changes in detached worktree
`~/staff-builds/runner-dashboard-a826992`. The repository's
`deploy/package-dashboard-artifact.sh --python-minor 3.11` built the frontend,
verified all 37 wheels against Python 3.11, and passed its offline installation,
dependency, runtime-import, and artifact checks. The service was stopped only
after confirming no active staff runs; the full deployment was backed up before
the repository artifact installer replaced files.

- Artifact: `~/staff-bundle/rd-a826992-py311/dashboard-4.10.0.tar.gz`.
- SHA-256: `4fd9c331e583d21891cc276d967d653b1a4a960b0710c434ed38f80dd59a3d03`.
- Full previous deployment: `~/actions-runners/dashboard.bak-2026-09-23-123418`.
- Environment backup: `~/.config/runner-dashboard/env.bak-2026-09-23-122051`.
- Drop-in backup: `/etc/systemd/system/runner-dashboard.service.d/staff-hub.conf.bak-2026-09-23-122051`.
- Login-shell backup: `~/.profile.bak-2026-09-23`.
- Windows forwarding backup: `C:\Users\diete\Repositories\_deploy\portproxy.bak-2026-09-23-123323.txt`.

Backup exception: nvm appended initialization to the pre-existing `.bashrc`
before a pre-change backup was made. The later `.profile` and service changes
were backed up before editing.

For rollback, first stop the dashboard and preserve the current deployment
directory. Restore the complete previous deployment at its original absolute
path, then start the service; retain the scheduler-off environment file. To
undo networking, remove only `StaffHub-Ollama-WSL` and the portproxy entry for
`192.168.208.1:11434`, using an administrator shell. Preserve unrelated rules.
Neither backup deletion nor rollback is part of routine verification.

## Initial six-provider verification and repeat checks

| Provider | Successful dashboard run | Result |
| --- | --- | --- |
| `claude` | `run-4cd625e1e8d6` | `succeeded`, exit 0, `STAFF_RESULT: ok` |
| `codex` | `run-de78a17520d0` | `succeeded`, exit 0, `STAFF_RESULT: ok` |
| `antigravity` | `run-ca0beeaed98f` | `succeeded`, exit 0, `STAFF_RESULT: ok` |
| `cursor-agent` | `run-78f6011fd0d4` | `succeeded`, exit 0, `STAFF_RESULT: ok` |
| `ollama` (via Codex) | `run-8ce7e91178b8` | `succeeded`, exit 0, `STAFF_RESULT: ok` |
| `claude-ollama` | `run-d4c83ae38059` | `succeeded`, exit 0, `STAFF_RESULT: ok` |

Antigravity initially failed in `run-8cb01018890e` with WSL
`UtilAcceptVsock:271: accept4 failed 110`; one retry succeeded. Both the configured
`/run/WSL/1_interop` socket and an inherited WSL session socket could launch a
harmless Windows command during diagnosis. Repeated failures need investigation;
an available-provider flag alone does not prove Windows interop or authentication.

Run checks locally on OGLaptop. Save WSL commands as a script and launch it from
PowerShell with `wsl.exe -d Ubuntu -- bash --noprofile --norc <Linux-script-path>`.
Do not run `wsl.exe` over SSH. These read-only checks expose no credential values:

```bash
systemctl is-active runner-dashboard
grep '^STAFF_SCHEDULER_ENABLED=' ~/.config/runner-dashboard/env
curl -fsS http://127.0.0.1:8321/api/health
curl -fsS 'http://127.0.0.1:8321/api/staff/board?local=1'
gateway=$(ip route | awk '/default/{print $3; exit}')
curl -fsS --max-time 8 "http://$gateway:11434/api/version"
```

For an authorized active provider check, submit the following request, replacing
`claude` with each provider ID in the table. Poll `GET /api/staff/runs/<id>` for
the returned run ID until terminal status; require `succeeded` and the result
marker, not merely HTTP 200 or `queued`. The CSRF header is required even on
loopback. These calls use the node's configured loopback authentication;
do not disable authentication if a differently configured node rejects them.

```bash
curl -fsS http://127.0.0.1:8321/api/staff/ad-hoc/run \
  -H 'Content-Type: application/json' \
  -H 'X-Requested-With: XMLHttpRequest' \
  --data '{"provider":"claude","machine":"local","prompt":"Health check: do not change anything. Reply OK, then STAFF_RESULT: ok"}'
```

Sign-in troubleshooting: Claude's waiting terminals had keyboard echo disabled.
The owner successfully completed login after submitting the code despite no
visible characters. Prefer the owner's own interactive terminal, keep every
code private, and avoid repeatedly creating competing sign-in sessions.

The requested setup and six checks are complete. This does not establish
unattended reliability across reboots, future token refreshes, subnet changes,
or real repository-editing jobs. Keep the scheduler off unless Dieter explicitly
authorizes a later scheduling change.
