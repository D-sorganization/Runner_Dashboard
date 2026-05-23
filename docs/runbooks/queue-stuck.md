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

## Stale Run Classification

The `backend/queue_cleanup.py` module classifies stale runs by root cause.
Knowing the cause narrows remediation:

| Cause | Description |
|---|---|
| `superseded_pr_head` | A newer commit pushed to the PR branch; old run will never start |
| `closed_or_deleted_ref` | PR closed or branch deleted while run was queued |
| `abandoned_agent` | Agent workflow dispatch that was never picked up (agent crashed) |
| `stale_feature_branch` | Long-lived feature branch with no recent activity |
| `offline_runner_or_lag` | Runner went offline or GitHub queuing lag |
| `stale_main_branch` | Default-branch run queued for too long |
| `unknown` | Does not match any heuristic above |

Superseded PR-head runs differ from unsatisfiable runner label runs:
a superseded run will never proceed regardless of runner availability, while an
unsatisfiable label run would proceed if a matching runner came online.
Use `dry_run=true` to preview which category each run falls into before purging.

## Mitigation

```bash
# A. Preview stale runs without cancelling anything (dry-run).
#    Use min_age_minutes to lower the age threshold for diagnosis.
curl -fsS -X POST \
  "http://localhost:8321/api/queue/purge-stale?dry_run=true&min_age_minutes=30" \
  | jq '.'

# B. Bulk purge stale runs (calls queue_cleanup.purge_stale_runs).
#    The Queue Health tab "Purge stale" button calls the same endpoint.
#    Default min_age_minutes is 60 — increase to be conservative.
curl -fsS -X POST \
  "http://localhost:8321/api/queue/purge-stale?min_age_minutes=60" \
  | jq '.'

# C. List stale runs (read-only scan, no cancellations).
curl -fsS \
  "http://localhost:8321/api/queue/stale?min_age_minutes=30" \
  | jq '.'

# D. Cancel a single specific run via the dashboard.
curl -fsS -X POST \
  http://localhost:8321/api/runs/<repo>/cancel/<run_id>

# E. Cancel directly via gh as a fallback.
gh run cancel <run_id> --repo D-sorganization/<repo>

# F. If the stall is caused by missing label coverage, scale runners up.
sudo systemctl restart runner-autoscaler
# or manually start an idle runner unit
sudo systemctl start 'actions.runner.D-sorganization-<repo>.<runner>.service'
```

When to use `dry_run` vs purge:
- Always run `dry_run=true` first on production to verify the affected runs.
- Use purge only after confirming the dry-run list is correct.
- Adjust `min_age_minutes` to match job duration expectations (default 60 min).

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

## Post-Incident Checklist

After resolving a queue stall, verify the following:

- [ ] `curl -fsS http://localhost:8321/api/queue/stale | jq 'length'` returns 0
      (or a count you accept as baseline noise).
- [ ] `curl -fsS http://localhost:8321/api/queue/diagnose | jq '.'` shows no
      critical lag for any label.
- [ ] `crontab -l | grep scheduled-dashboard-maintenance` — cron entry is present.
- [ ] Most recent maintenance log is within the last 90 minutes:
      `ls -lt ~/.cache/runner-dashboard/maintenance.log | head -3`
- [ ] Runner label coverage is correct — all labels in active workflows have at
      least one matching runner registered in `backend/machine_registry.yml`.
- [ ] If a `superseded_pr_head` or `abandoned_agent` run caused the stall,
      verify the responsible PR/branch has been cleaned up.
- [ ] File a postmortem if the stall lasted > 30 minutes or affected > 1 repo.

## Postmortem Template

[`postmortem-template.md`](./postmortem-template.md)
