# Interactive scheduled tasks popping console windows (focus steal)

## Symptom

Console (PowerShell/cmd/Git Bash) windows appear on the desktop "out of
nowhere" every few minutes, stealing keyboard/mouse focus mid-typing. On
DeskComputer this was a visible window every 5 minutes that stayed open for
the duration of two SSH round-trips; on ControlTower, every 30 minutes.

## Root cause

Any scheduled task registered with an **InteractiveToken** principal ("run
only when user is logged on") whose action is a **console-subsystem
executable** (`powershell.exe`, `pwsh.exe`, `cmd.exe`, `bash.exe`) creates a
visible console window in the user's session each time it fires. The window
takes foreground focus.

`-WindowStyle Hidden` is **not sufficient**: the console host window is
created by the loader before PowerShell ever parses its arguments, so a
focus-stealing flash remains on every start.

Tasks running as **S4U** ("run whether user is logged on or not") or
**SYSTEM** are immune — they execute in a non-interactive session with no
desktop. Prefer S4U for new tasks that don't need the interactive session
(see `install-wsl-keepalive-task.ps1` for the pattern). The launcher below
exists for tasks that **must** stay InteractiveToken — anything that has to
reach the logged-on user's WSL instance or desktop apps.

## Fix

`deploy/run-hidden.vbs` + `deploy/install-hidden-task-launcher.ps1`.

The installer rewrites the task action from

```
<exe> <args>
```

to

```
wscript.exe //B //Nologo "<path>\run-hidden.vbs" <exe> <args>
```

`wscript.exe` is a GUI-subsystem host, and `WshShell.Run(cmd, 0, True)`
launches the child with `SW_HIDE`: **no console window is ever created** —
no flash, no focus steal. The VBS waits for the child and propagates its
exit code, so task history / `LastTaskResult` stay accurate. Triggers,
settings, and principal are untouched.

Contracts (enforced, tested in `tests/deploy/test_hidden_task_launcher.py`):

- idempotent — wrapping a wrapped task is a no-op;
- reversible — `-Revert` restores the original action verbatim;
- postcondition-verified — the installer re-reads the task after writing and
  fails loudly on any mismatch;
- foldered tasks are addressed by their discovered `TaskPath`
  (`Get-ScheduledTask` finds them anywhere but `Set-ScheduledTask` without
  `-TaskPath` fails with 0x80070002 — found live on ControlTower's
  `\D-sorganization\matlab-runner`);
- arguments containing literal double quotes are refused (WScript strips
  quoting during argument parsing, so they cannot be rebuilt losslessly);
- the non-elevated `schtasks /Change` fallback refuses command lines over
  261 chars (silent truncation limit).

## Install on a host

```powershell
# 1. Stage both files together (they must sit in the same directory):
Copy-Item deploy\run-hidden.vbs, deploy\install-hidden-task-launcher.ps1 `
    -Destination C:\Users\<user>\runner_fleet_monitor\

# 2. Wrap each offending task (repeat per task; -DryRun to preview):
powershell -NoProfile -File C:\Users\<user>\runner_fleet_monitor\install-hidden-task-launcher.ps1 `
    -TaskName 'RunnerFleet-Health-Monitor'

# Revert if ever needed:
powershell -NoProfile -File ...\install-hidden-task-launcher.ps1 -TaskName '<name>' -Revert
```

Find candidates on a host:

```powershell
Get-ScheduledTask | Where-Object { $_.State -ne 'Disabled' -and
    [string]$_.Principal.LogonType -eq 'Interactive' } |
  ForEach-Object { $t = $_; $t.Actions |
    Where-Object { $_.Execute -match 'powershell|pwsh|cmd\.exe|bash' } |
    ForEach-Object { "$($t.TaskPath)$($t.TaskName)  ->  $($_.Execute)" } }
```

Verify a wrapped task runs windowless: fire it, then confirm the child's
`MainWindowHandle` is `0` while it runs and `LastTaskResult` is `0` after.

## Fleet state (rollout 2026-07-30)

| Host         | Task                                                    | Action                                                                                           |
| ------------ | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| DeskComputer | `RunnerFleet-Health-Monitor` (5 min)                    | wrapped                                                                                          |
| DeskComputer | `Forgejo-Backup-Sync` (daily 03:00)                     | wrapped                                                                                          |
| DeskComputer | Startup `start_docker.bat`                              | replaced by `start_docker_hidden.lnk` → launcher; bat moved to `C:\Users\diete\start_docker.bat` |
| ControlTower | `Conductor-RealWork-RM-30min` (30 min)                  | wrapped                                                                                          |
| ControlTower | `D-sorg GitHub Forgejo Posture Backup`                  | wrapped                                                                                          |
| ControlTower | `WSL-VHDX-Compact-Overnight`                            | wrapped                                                                                          |
| ControlTower | `\D-sorganization\matlab-runner`                        | wrapped                                                                                          |
| OGLaptop     | — (keepalive already S4U; no interactive console tasks) | launcher staged only                                                                             |

Not wrapped on purpose: S4U/SYSTEM tasks (no UI by construction), Microsoft
system tasks (`\Microsoft\...`), and OGLaptop's `RebootToUbuntu`
(user-invoked deliberate reboot; a flash there is moot).

Known remainder: DeskComputer's `WSL-Runner-KeepAlive` was registered from an
elevated context, so rewriting its action needs an elevated session (both
`Set-ScheduledTask` and the `schtasks` fallback are denied non-elevated, and
elevating non-interactively would itself pop UAC). Low impact — the task is
resident, so its `-WindowStyle Hidden` flash occurs only at logon or when the
health monitor restarts a dead instance. To finish it, run once from an
elevated PowerShell:

```powershell
powershell -NoProfile -File C:\Users\diete\runner_fleet_monitor\install-hidden-task-launcher.ps1 `
    -TaskName 'WSL-Runner-KeepAlive'
```

## Related

- `docs/runbooks/wsl-keepalive-probe-fix.md` — the keepalive watchdog this
  monitor babysits.
- `deploy/install-wsl-keepalive-task.ps1` — S4U registration pattern for
  tasks that don't need the interactive session.
