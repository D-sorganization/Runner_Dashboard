# Fleet backup: rotated encrypted USB SSD offsite copy (#4009)

Offsite copies use **restic copy** from the primary fleet backup repository (`SOURCE_RESTIC_REPOSITORY`) into a **second restic repository** on a BitLocker-protected USB SSD. Two drives rotate by ISO week parity (slot **A** vs **B**) so one drive can stay off-site while the other is refreshed.

## Prerequisites

- Primary restic backups are healthy (OGLaptop pilot #4015 or successor).
- Two USB SSDs, encrypted (BitLocker), labeled `FLEET-OFFSITE-A` and `FLEET-OFFSITE-B` (override via env file).
- `restic` on PATH; `RESTIC_PASSWORD` (source) and `RESTIC_PASSWORD2` (USB repo) set in the task environment — never commit passwords.

## Configuration

Copy `deploy/fleet-backup-offsite.env.example` to `fleet-backup-offsite.env` on the operator host. Adjust labels and the source repository URL.

## Dry run

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/fleet-backup-offsite-usb.ps1 -DryRun `
  -ConfigFile C:\ProgramData\RunnerDashboard\fleet-backup-offsite.env
```

## Scheduled task

Register a weekly task (owner-run) that mounts/unlocks the active USB, exports restic passwords, then runs the script without `-DryRun`. Store state under `%ProgramData%\RunnerDashboard\fleet-backup-offsite-state.json`.

## Rollout

Issue #4009 waits on the OGLaptop pilot (#4015). Do not point `SOURCE_RESTIC_REPOSITORY` at production data until pilot last-success markers are green on the hub.
