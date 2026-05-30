# Runbook: WSL Keepalive Probe Fix & Fleet Rollout

The Windows-side WSL keepalive watchdog (`deploy/wsl-keepalive.ps1`) could
declare a perfectly healthy WSL distro "unresponsive" on every cycle. On hosts
running the legacy **Watchdog** mode this drove a `wsl --shutdown` reboot loop
that took the entire runner fleet (and the dashboard) offline repeatedly. This
runbook explains the root cause, the fix, and how to roll it out consistently
across every host.

## Symptom

- Runners on a host flap offline/online every minute or two; GitHub shows them
  disconnecting and reconnecting.
- `journalctl --list-boots` inside WSL shows many short boots minutes apart
  (some only seconds long) instead of one long uptime.
- `Get-ScheduledTaskInfo` shows the keepalive task restarting frequently.
- The keepalive JSONL log (`%LOCALAPPDATA%\runner-dashboard\wsl-keepalive.log`)
  is full of `unresponsive_detected` / `recovery_failed` (Watchdog) or
  `unresponsive_no_wsl_reset` (Resident) events even though a direct
  `wsl -d <distro> -- id` returns instantly.

## Severity

**P1** when a host is in Watchdog mode — the whole fleet on that host cycles
offline. **P3** in Resident mode — no WSL reset, but health reporting and
dashboard auto-recovery are broken.

## Root cause

`Test-Responsive` decided success two ways, both wrong:

1. It gated on the probe process exit code (`$p.ExitCode`). But
   `Start-Process -PassThru` does **not** populate the exit code when the
   watchdog runs non-interactively (`powershell -File` from a scheduled task) —
   it comes back `$null`, which compared as "non-zero" and failed every probe.
2. The fallback "non-empty stdout" idea is also unsafe: `wsl.exe` writes its
   _own_ errors (e.g. `There is no distribution with the supplied name.`) to
   **stdout**, so even an offline distro yields output.

Additionally the probe command `/bin/sh -c 'echo alive'` is split by
`Start-Process -ArgumentList` (it does not quote multi-word elements), so it
ran `sh -c echo` and produced no output regardless.

## Fix (shipped in `deploy/wsl-keepalive.ps1`)

- The probe runs a **single-token** command, `id` (no argument-splitting
  hazard).
- Success is decided by a pure, unit-tested helper `Test-ProbeSuccess`:
  the probe must exit within the timeout **and** its stdout must contain the
  sentinel `uid=` (always present in `id` output, never in wsl.exe
  diagnostics). The process exit code is never consulted.

Regression coverage: `tests/deploy/test_wsl_keepalive_script.py`
(`test_probe_does_not_gate_on_process_exit_code`,
`test_probe_success_helper_ignores_exit_code`).

## Fleet rollout (do this on every host)

The fix is universal; every host must pick it up and run the **Resident**
keepalive task (which never resets WSL), not the legacy Watchdog task.

1. **Update the deployed dashboard** so the host has the fixed script:

   ```bash
   bash deploy/update-deployed.sh --repo /path/to/runner-dashboard
   ```

2. **Install/refresh the Resident keepalive task**, passing the host's actual
   distro name:

   ```powershell
   pwsh -File deploy\install-wsl-keepalive-task.ps1 -Distro <distro-name>
   ```

   This registers `RunnerDashboard-WSL-Resident-KeepAlive` in Resident mode
   with startup + logon triggers.

3. **Retire any legacy Watchdog task** (e.g. `WSL-Runner-KeepAlive`) so a host
   never has two keepalives, and the destructive `wsl --shutdown` path is gone:

   ```powershell
   Stop-ScheduledTask    -TaskName 'WSL-Runner-KeepAlive' -ErrorAction SilentlyContinue
   Unregister-ScheduledTask -TaskName 'WSL-Runner-KeepAlive' -Confirm:$false -ErrorAction SilentlyContinue
   ```

4. **Verify** on the host:

   ```powershell
   pwsh -File deploy\wsl-keepalive.ps1 -Once -Distro <distro-name> -Mode Resident `
       -CheckIntervalSeconds 10 -ProbeTimeoutSeconds 5 -LogDir $env:TEMP\kpcheck
   # expect: {"outcome":"healthy",...}
   ```

   Inside WSL, confirm uptime climbs steadily (`uptime -p`) and runners stay
   `active`.

## Consistency guardrails

- The dashboard's keepalive inspector
  (`backend/diagnostics/keepalive_inspector.py`) surfaces task state per host;
  use it to spot hosts still on a legacy task or a stale script. Its
  `WSL_KEEPALIVE_TASK_NAME` constant should track the Resident task name as
  hosts migrate.
- Never hand-edit the deployed `wsl-keepalive.ps1` on a host — that is how
  copies drift from this canonical source. Always change `deploy/` and
  redeploy via `update-deployed.sh`.
