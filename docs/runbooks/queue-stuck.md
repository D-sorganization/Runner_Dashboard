# Runbook: Queue Stuck

GitHub Actions queue depth is growing without draining; workflow runs are
stuck in `queued` or `in_progress` long past their normal duration.

## Symptom

- Queue Health tab shows persistent or growing `queued_count` /
  `in_progress_count`.
- Dashboard `/api/queue/status` shows old queued runs with long
  `queue_wait_seconds`.
- Dashboard stale preview or the queued-job reaper reports
  `unsatisfiable_runner_labels` or `superseded_pr_head` candidates.
- Workflow dispatch jobs never start even with idle runners visible.

## Severity

**P1** if no work is draining at all.
**P2** if one runner label, repo, or workflow family is stuck.
**P3** if only a few old, superseded runs are stuck.

## Stale Reasons

- `unsatisfiable_runner_labels`: no online self-hosted runner advertises the
  requested labels. These are safe to cancel once older than the configured
  threshold because they cannot start until the workflow or runner labels are
  repaired.
- `superseded_pr_head`: a queued PR run targets an older head SHA while a newer
  run exists for the same PR/workflow. These are safe to cancel only when the
  classifier marks `safe_to_cancel=true`; never cancel the current PR head.
- Busy but current-head jobs are not stale. Leave them alone unless an operator
  has confirmed the run is not required.

Do not cancel current-head required checks, release/tag workflows, deployment
workflows, or running jobs as part of routine queue cleanup.

## Detection

```bash
# 1. Current queue with timing data.
curl -fsS http://localhost:8321/api/queue/status | jq '.'

# 2. Stale API preview, when the endpoint is available on the deployed build.
curl -fsS 'http://localhost:8321/api/queue/stale?min_age_minutes=30' | jq '.'

# 3. Direct GitHub query for queued runs older than 30 minutes.
gh api -X GET 'repos/D-sorganization/Runner_Dashboard/actions/runs' \
  -f status=queued --jq '.workflow_runs[] | {id,name,head_branch,head_sha,created_at,status}'

# 4. Are runners idle while jobs queue?
gh api orgs/D-sorganization/actions/runners \
  --jq '.runners[] | {name, status, busy, labels: [.labels[].name]}'

# 5. Inspect dashboard logs for stale/cancel API failures.
sudo journalctl -u runner-dashboard -n 200 --no-pager | grep -i 'queue\|cancel\|stale'
```

## Stale Run Classification

The `backend/queue_cleanup.py` module classifies stale runs by root cause.
Knowing the cause narrows remediation:

| Cause                   | Description                                                      |
| ----------------------- | ---------------------------------------------------------------- |
| `superseded_pr_head`    | A newer commit pushed to the PR branch; old run will never start |
| `closed_or_deleted_ref` | PR closed or branch deleted while run was queued                 |
| `abandoned_agent`       | Agent workflow dispatch that was never picked up (agent crashed) |
| `stale_feature_branch`  | Long-lived feature branch with no recent activity                |
| `offline_runner_or_lag` | Runner went offline or GitHub queuing lag                        |
| `stale_main_branch`     | Default-branch run queued for too long                           |
| `unknown`               | Does not match any heuristic above                               |

Superseded PR-head runs differ from unsatisfiable runner label runs:
a superseded run will never proceed regardless of runner availability, while an
unsatisfiable label run would proceed if a matching runner came online.
Use `dry_run=true` to preview which category each run falls into before purging.

## Mitigation

Always preview before cancellation.

```bash
# A. Preview stale jobs older than 30 minutes (dry-run, no cancellations).
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

# D. In workflow form, preview only unsatisfiable label candidates.
gh workflow run util-queued-job-reaper.yml \
  -f dry-run=true \
  -f min-age-minutes=30 \
  -f reason-filter=unsatisfiable_runner_labels \
  -f max-cancel=10

# E. Cancel only if the stale API/reaper preview marks the candidates safe.
gh workflow run util-queued-job-reaper.yml \
  -f dry-run=false \
  -f min-age-minutes=30 \
  -f reason-filter=unsatisfiable_runner_labels \
  -f max-cancel=5

# F. Cancel a single specific run via the dashboard.
curl -fsS -X POST \
  http://localhost:8321/api/runs/<repo>/cancel/<run_id>

# G. Cancel directly via gh as a fallback.
gh run cancel <run_id> --repo D-sorganization/<repo>

# Missing-label stalls need runner/workflow repair, not broad cancellation.
sudo systemctl restart runner-autoscaler
sudo systemctl start 'actions.runner.D-sorganization-<repo>.<runner>.service'
```

When to use `dry_run` vs purge:

- Always run `dry_run=true` first on production to verify the affected runs.
- Use purge only after confirming the dry-run list is correct.
- Adjust `min_age_minutes` to match job duration expectations (default 60 min).

If the deployed build does not expose `/api/queue/stale`, use the workflow
dry-run summary and direct `gh` inspection.
`deploy/scheduled-dashboard-maintenance.sh` will warn instead of silently
claiming stale cleanup succeeded. If cron was disabled, re-enable it.

## Scheduled Maintenance

`deploy/scheduled-dashboard-maintenance.sh` runs stale cleanup in safe-preview
mode by default:

```bash
STALE_QUEUE_DRY_RUN=1 STALE_QUEUE_AGE_MINUTES=120 \
  bash deploy/scheduled-dashboard-maintenance.sh --skip-git
```

For an incident run, set `STALE_QUEUE_MAX_CANCEL` and a reason filter before
enabling cancellation. If the dashboard or fallback script cannot enforce caps,
the maintenance script refuses an uncapped purge.

```bash
STALE_QUEUE_DRY_RUN=0 \
STALE_QUEUE_AGE_MINUTES=30 \
STALE_QUEUE_REASON_FILTER=superseded_pr_head \
STALE_QUEUE_MAX_CANCEL=5 \
  bash deploy/scheduled-dashboard-maintenance.sh --skip-git
```

## Post-Incident Checklist

1. Refresh `/api/queue/status` and confirm queue depth is falling.
2. Re-run stale preview and confirm only expected reasons remain.
3. Check runner idle/busy count and label coverage.
4. Verify alerts or stale badges auto-close after the next dashboard poll.
5. If `superseded_pr_head` repeats, add or fix workflow concurrency for that
   PR workflow instead of relying on cleanup.

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
