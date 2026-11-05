# node-b WSL export into restic (#4011)

Fleet backup **PR 3 of 3** (panel `th_958c7b53ae45`): after the Windows-path pilot (#4015) backs up selected directories with VSS, this script exports the **node-b** workstation WSL distribution to a tar archive and runs `restic backup` on that tar into the **same** rest-server repository.

## Preconditions

- Desk rest-server hub (#4017) and node-b pilot client (#4015) are configured.
- `restic` and `wsl.exe` are on PATH for the operator account.
- **node-b WSL must be healthy** before a live run. While WSL is broken, use `-DryRun` only (repo CI validates scripts without calling WSL).

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

## Verification

- Footer check: last 1024 bytes of the export tar must be zero (truncated exports fail closed).
- `restic snapshots --tag wsl-export` should list the new snapshot after a live run.

## Related

- Epic #3874 / fleet backup panel `th_958c7b53ae45`
- Pilot (#4015), hub (#4017), client (#4018)
