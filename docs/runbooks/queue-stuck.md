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

## Mitigation

Always preview before cancellation.

```bash
# Preview stale jobs older than 30 minutes.
curl -fsS -X POST http://localhost:8321/api/queue/purge-stale \
  -H 'Content-Type: application/json' \
  -d '{"min_age": 30, "dry_run": true}' | jq '.'

# In workflow form, preview only unsatisfiable label candidates.
gh workflow run util-queued-job-reaper.yml \
  -f dry-run=true \
  -f min-age-minutes=30 \
  -f reason-filter=unsatisfiable_runner_labels \
  -f max-cancel=10

# Cancel only if the stale API/reaper preview marks the candidates safe.
gh workflow run util-queued-job-reaper.yml \
  -f dry-run=false \
  -f min-age-minutes=30 \
  -f reason-filter=unsatisfiable_runner_labels \
  -f max-cancel=5

# Cancel a specific run after confirming it is not current-head required CI.
curl -fsS -X POST http://localhost:8321/api/runs/<repo>/cancel/<run_id>

# Missing-label stalls need runner/workflow repair, not broad cancellation.
sudo systemctl restart runner-autoscaler
sudo systemctl start 'actions.runner.D-sorganization-<repo>.<runner>.service'
```

If the deployed build does not expose `/api/queue/stale`, use the workflow
dry-run summary and direct `gh` inspection. `deploy/scheduled-dashboard-maintenance.sh`
will warn instead of silently claiming stale cleanup succeeded.

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

## Postmortem Template

[`postmortem-template.md`](./postmortem-template.md)
