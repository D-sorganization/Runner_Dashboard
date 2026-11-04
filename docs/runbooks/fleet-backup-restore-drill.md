# Fleet backup restore drill (#4013)

Two **verified restore drills** must be recorded on the machine backup record before any `restic forget` / `restic prune` (see #4020). Weekly read-data-subset checks do **not** count.

## Operator script (automated)

From the repo root on the backed-up machine (after the OGLaptop pilot #4015 is taking snapshots):

```bash
python3 deploy/fleet-backup/restic_restore_drill.py \
  --machine oglaptop \
  --record ~/.config/runner-dashboard/fleet_backup_machine_record.json \
  --snapshot latest \
  --target /var/tmp/fleet-restic-drill \
  --probe-manifest deploy/fleet-backup/probe_manifest.json
```

Environment:

- `RESTIC_REPOSITORY`, `RESTIC_PASSWORD`, and rest-server credentials as for scheduled backup.
- Optional hub alert on failure: `HUB_URL`, `HUB_FLEET_TOKEN` (posts to `/api/fleet/backup/drill-failure` when the hub route is deployed).

Run the command **twice** on different days (or after two distinct snapshots). Confirm the record shows two verified `restore_drill` events before approving prune/forget.

## Probe manifest

Copy `deploy/fleet-backup/probe_manifest.example.json` to a local `probe_manifest.json`. After the first successful backup, set each file's `sha256` to the digest of a small path that restic always backs up (for example `python3 -c "import hashlib,pathlib; p=pathlib.Path('REL'); print(hashlib.sha256(p.read_bytes()).hexdigest())"`).

## Owner-only checklist (not automated)

Complete manually and keep evidence outside git:

1. [ ] Pick a snapshot date/time and ticket/note for drill #1.
2. [ ] Restore to an isolated directory (not production paths); do not start services from restored data.
3. [ ] Open one restored document or config and confirm it matches expectations.
4. [ ] Record drill #1 in the machine JSON (via script success) and archive the log.
5. [ ] Repeat for drill #2 on a **different** snapshot.
6. [ ] Only after two verified drills, approve `restic forget` / `restic prune` on this machine.

If a drill fails, the record is **halted**, the hub is alerted, and operators must fix root cause before retrying (no silent auto-retry).

## Tests

```bash
pytest tests/deploy/test_restic_restore_drill.py -q
```
