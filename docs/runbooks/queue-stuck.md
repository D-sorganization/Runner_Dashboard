# Runbook: Queue Stuck

GitHub Actions queue depth is growing without draining; workflow runs are
stuck in `queued` or `in_progress` long past their normal duration.

## Symptom

- Queue Health tab shows persistent or growing `queued_count` /
  `in_progress_count`.
- Dashboard's `/api/queue` returns runs older than the configured stale
  threshold (default driven by `backend/queue_cleanup.py`).
- Workflow_dispatch agent jobs never start, even with idle runners visible.
- "Stale runs" badge appears in the Queue Health tab.

## Severity

**P1** if no work is draining at all (full queue stall).
**P2** if some labels drain but a specific subset is stuck and CI work
is partially blocked.
**P3** if a single old run is stuck but the rest of the queue flows.

## Detection

- Queue Health tab highlights stale runs.
- `curl -fsS http://localhost:8321/api/queue/stale` returns a non-empty list
  (this endpoint is backed by `backend/queue_cleanup.find_stale_runs`).
- `curl -fsS http://localhost:8321/api/queue/diagnose` shows runs older than
  the threshold.
- `gh run list --status queued --limit 50` shows runs older than ~30 min.
- Operators on slack/Discord report jobs not starting.

## Diagnosis

```bash
# 1. Get the dashboard's view of stale runs (uses queue_cleanup.py helpers).
curl -fsS http://localhost:8321/api/queue/stale | jq '.'

# 2. Drill into queue diagnostics for cross-repo summary.
curl -fsS http://localhost:8321/api/queue/diagnose | jq '.'

# 3. Direct GitHub query for any queued/in-progress runs older than 30 min.
gh api -X GET 'repos/D-sorganization/Runner_Dashboard/actions/runs' \
  -f status=queued --jq '.workflow_runs[] | {id,name,created_at,status}'

# 4. Are runners idle while jobs queue? (label mismatch is the usual cause.)
gh api orgs/D-sorganization/actions/runners \
  --jq '.runners[] | {name, status, busy, labels: [.labels[].name]}'

# 5. Inspect recent dashboard logs for cancellation failures.
sudo journalctl -u runner-dashboard -n 200 --no-pager | grep -i 'queue\|cancel\|stale'
```

## Mitigation

```bash
# A. Use the dashboard's bulk purge (calls queue_cleanup.purge_stale_runs).
#    The Queue Health tab "Purge stale" button calls the same endpoint.
curl -fsS -X POST http://localhost:8321/api/queue/purge-stale | jq '.'

# B. Cancel a single specific run via the dashboard.
curl -fsS -X POST \
  http://localhost:8321/api/runs/<repo>/cancel/<run_id>

# C. Cancel directly via gh as a fallback.
gh run cancel <run_id> --repo D-sorganization/<repo>

# D. If the stall is caused by missing label coverage, scale runners up.
sudo systemctl restart runner-autoscaler
# or manually start an idle runner unit
sudo systemctl start 'actions.runner.D-sorganization-<repo>.<runner>.service'
```

The hourly `deploy/scheduled-dashboard-maintenance.sh` cron also performs
stale-queue purges; if cron was disabled, re-enable it.

## Resolution

- Tune the staleness threshold in `backend/queue_cleanup.py` if real long
  jobs are being false-positived as stale.
- If a workflow consistently produces stuck runs, add a `timeout-minutes:`
  cap to the job and a regression test in `tests/api/` covering the cancel
  path.
- Confirm `deploy/scheduled-dashboard-maintenance.sh` is in cron and ran in
  the last hour:
  ```bash
  crontab -l | grep scheduled-dashboard-maintenance
  ls -lt ~/.cache/runner-dashboard/maintenance.log | head -3
  ```
- If a label-mismatch caused the stall, fix workflow `runs-on:` or update
  `backend/machine_registry.yml` so dispatch targets a label that exists.
- File a postmortem if the stall affected more than one repo.

## Postmortem Template

[`postmortem-template.md`](./postmortem-template.md)
