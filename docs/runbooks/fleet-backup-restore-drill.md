# Fleet backup restore drill (#4013)

Record **two verified restore drills** on the machine JSON before `restic forget` / `restic prune` (#4020). Weekly read-data-subset checks do not count.

## Script

```bash
python3 deploy/fleet-backup/restic_restore_drill.py \
  --machine oglaptop \
  --record ~/.config/runner-dashboard/fleet_backup_machine_record.json \
  --snapshot latest \
  --target /var/tmp/fleet-restic-drill \
  --probe-manifest deploy/fleet-backup/probe_manifest.json
```

Requires restic env (`RESTIC_REPOSITORY`, `RESTIC_PASSWORD`, …). Optional failure alert: `HUB_URL` + `HUB_FLEET_TOKEN` → `/api/fleet/backup/drill-failure` when deployed (#4020). Run twice (distinct snapshots); confirm two `restore_drill` events before prune.

Probe manifest: `{"files":[{"path":"REL/PATH","sha256":"<sha256>"}]}` for files always in backup scope.

## Owner-only checklist

1. [ ] Note snapshot id/time for drill #1; restore to an isolated directory only.
2. [ ] Manually open one restored file; confirm content; keep log outside git.
3. [ ] Script success appends verified event; repeat for drill #2 on another snapshot.
4. [ ] Approve prune/forget only after two verified events. Failed drill halts the record (fix before retry; no silent retry).

Tests: `pytest tests/deploy/test_restic_restore_drill.py -q`
