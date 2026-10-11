# Runbook: Fleet host memory policy

Origin: Repository_Management#2071 (survey 2026-10-11). Under sustained CI load
every WSL VM sat at its `memory=` cap because of Linux page cache, not real use
(ControlTower 48 GB of 128 GB cache for 9 GB real use). `autoMemoryReclaim` only
fires when the VM is idle, which runner hosts never are.

## wsl-cache-trim (every runner distro)

- `deploy/wsl-cache-trim` is installed to `/usr/local/sbin/wsl-cache-trim`,
  with `wsl-cache-trim.service` and `wsl-cache-trim.timer` in
  `/etc/systemd/system/`. The timer runs 5 min after boot and every 15 min.
- When `Cached` is at least `THRESH`% of `MemTotal` (default 30) it runs
  `sync`, drops the page cache, compacts memory and logs with
  `logger -t wsl-cache-trim`. Below the threshold it does nothing.
- Installed and enabled by `deploy/install-runner-maintenance.sh`, which
  `deploy/deploy-host.sh` and `deploy/setup.sh` run. Idempotent; uses `sudo -n`.
- Change the threshold with a drop-in:
  `systemctl edit wsl-cache-trim.service` -> `[Service]` `Environment=THRESH=40`.
- Check: `systemctl list-timers wsl-cache-trim.timer`,
  `journalctl -t wsl-cache-trim -n 5`.
- Keep `.wslconfig` `memory=` at about half of host RAM and keep
  `autoMemoryReclaim`; both are harmless alongside the timer.

## Vendor service leak restart (Windows, where present)

`deploy/windows/install-vendor-leak-restart-task.ps1` registers the SYSTEM task
`Fleet-Dell-ServiceShell-Restart` (restart `DellClientManagementService` every
2 days at 04:30, log to `C:\ProgramData\fleet-dell-restart.log`). It exits
without changes on hosts that lack the service (only ControlTower has it).
Run once per host from an elevated PowerShell; it is not part of the WSL deploy
path. `-Remove` unregisters the task; `-WhatIf` previews.

## Scheduler drift note

`runner-scheduler` is copied to `/usr/local/bin/runner-scheduler` only by
`install-runner-maintenance.sh` (via `deploy-host.sh` / `setup.sh`). The routine
dashboard update, `update-deployed.sh`, never runs it, so a host updated only
that way keeps a stale scheduler (ControlTower stayed on the pre-#1922 copy).
Run `deploy/deploy-host.sh` after scheduler changes. The qualified OGLaptop
release path runs the scheduler from its release directory instead.
