# Recurring Post-Launch Scan

Issue #3086, epic #3077 (slice 8). Depends on slice 7 (#3085, merged).

`.github/workflows/recurring-scan.yml` runs daily on a GitHub-hosted runner
(`ubuntu-latest`; free, and keeps secret scanning off self-hosted runners on
a public repo per the slice 6 ruling on #3084). Each run scans only what
changed since the last one -- commits merged (diffing added lines) and
issues/comments created or updated (`gh api .../issues[/comments]?since=`)
-- against the publish guard's shared pattern list
(`config/publish/forbidden-patterns.json`, issue #3080), never a second,
drifting list.

## Alerts, never public comments

A finding never becomes a public issue, PR comment, or log line with the
matched text: `scripts/recurring_scan.py` records only a sanitized category
and location (`commit:abcdef012345`, `issue:142`) and raises one
`alert`-kind envelope through `scripts/rd_barb_notify.py`, the private
rd-barb relay other slices use.

## Heartbeat

Every run checks the cursor's `last_run_at` against now. Past the tolerance
(default 1.5x the schedule, `--max-gap-hours`), it raises a second alert
(`scan-heartbeat-missed`) first, so a disabled schedule or failing job is
noticed even though the run that would have caught it never happened.

## State and owner

The cursor (last scanned commit, last item timestamp, last run time) lives
on its own branch, `bot/recurring-scan-state`, rewritten with plumbing
commands (`git hash-object`, `git mktree`, `git commit-tree`) -- the same
reused-bot-branch pattern `collate-changes.yml` uses; the workflow never
writes to `main`. The repository owner (Dieter) owns this scan: review
alerts as they arrive, confirm the schedule stayed enabled, and update the
shared pattern list for a new category. `RD_BARB_WEBHOOK_URL`,
`RD_BARB_WEBHOOK_KEY` and `RD_BARB_THREAD_ID` must be configured for alerts
to leave the runner; unset, the notifier fails closed.
