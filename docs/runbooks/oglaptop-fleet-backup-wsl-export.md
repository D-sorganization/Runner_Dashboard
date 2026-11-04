# OGLaptop WSL export into restic (#4011)

Fleet backup **PR 3 of 3** (panel `th_958c7b53ae45`): after the Windows-path pilot (#4015) backs up selected directories with VSS, this script exports the OGLaptop WSL distribution to a tar archive and runs `restic backup` on that tar into the **same** rest-server repository.

## Preconditions

- Desk rest-server hub (#4017) and OGLaptop pilot client (#4015) are configured.
- `restic` and `wsl.exe` are on PATH for the operator account.
- **OGLaptop WSL must be healthy** before a live run. While WSL is broken, use `-DryRun` only (repo CI validates scripts without calling WSL).

## Configuration

Copy `deploy/fleet-backup-wsl-export.env.example` to:

`C:\ProgramData\runner-dashboard\fleet-backup-wsl-export.env`

| Key | Purpose |
|-----|---------|
| `RESTIC_REPOSITORY` | Same rest-server URL as the pilot |
| `RESTIC_PASSWORD_FILE` | restic password file (mode 600) |
| `WSL_DISTRO_NAME` | Distribution name from `wsl --list --verbose` |

Exports land under `C:\ProgramData\runner-dashboard\wsl-exports\`. State is written to `fleet_backup_wsl_export.json`.

## Operator commands

Dry-run (safe on broken WSL):

```powershell
cd <Runner_Dashboard>\deploy
.\oglaptop-fleet-backup-wsl-export.ps1 -DryRun
```

Live export + backup (when WSL works):

```powershell
.\oglaptop-fleet-backup-wsl-export.ps1
```

Re-backup an existing tar without re-exporting:

```powershell
.\oglaptop-fleet-backup-wsl-export.ps1 -SkipExport
```

## Integrity check

Before trusting a tar for restore, confirm the export finished: a complete `wsl --export` archive ends with **1024 zero bytes**. The script runs this check automatically after export (see `docs/runbooks/runner-registration-purge-recovery.md`).

## Non-goals

- No scheduled task in this PR (operator or a follow-up may chain after pilot `-InstallTask`).
- No cloud backends; tailnet rest-server only.

Refs #4011, #4015, #4017
