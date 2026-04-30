# Runbook: Deploy Rollback

A recent dashboard deploy introduced a regression — broken UI, failing
endpoints, crash-looping service, or a known-bad change. Roll back to the
prior deployed snapshot and stabilize before forward-fixing.

## Symptom

- The dashboard restarted after a deploy and is now crashing or returning
  5xx.
- A specific tab (Fleet, Maxwell, Queue Health, etc.) is throwing client-side
  errors after a recent push.
- `journalctl -u runner-dashboard` shows an `ImportError`, `SyntaxError`, or
  pydantic validation failure not seen before the deploy.
- The new `VERSION` is reported by `/api/version` but functionality is
  degraded.

## Severity

**P1** — any regression in production deserves immediate rollback. The
"Reversible" engineering principle in `CLAUDE.md` requires every deploy to
ship a rollback marker. Use it.

## Detection

- Spike in dashboard 5xx after `update-deployed.sh` ran.
- Operator reports correlated with the timestamp of `~/actions-runners/dashboard.bak.*`.
- `systemctl status runner-dashboard` shows recent restart counter.
- `git log -1 --format=%H ~/actions-runners/dashboard` (or VERSION file)
  matches a commit landed in the last hour.

## Diagnosis

```bash
# 1. Confirm the dashboard is running the recently-deployed code.
cat ~/actions-runners/dashboard/VERSION

# 2. List available rollback snapshots (created automatically by
#    deploy/update-deployed.sh before each deploy).
bash ~/actions-runners/dashboard/deploy/rollback.sh --list

# 3. Inspect logs since the last restart for a single root cause.
sudo journalctl -u runner-dashboard --since "30 minutes ago" --no-pager

# 4. Compare the deployed tree against the previous snapshot.
PREV=$(ls -dt ~/actions-runners/dashboard.bak.* | head -1)
diff -ruN "$PREV/backend" ~/actions-runners/dashboard/backend | head -200

# 5. Test the rollback non-destructively first.
bash ~/actions-runners/dashboard/deploy/rollback.sh --dry-run
```

## Mitigation

`deploy/rollback.sh` is the supported, reversible path. It rsyncs the most
recent (or specified) `.bak.*` snapshot back over the deploy directory and
restarts the systemd unit.

```bash
# A. Roll back to the most recent backup (the default).
bash ~/actions-runners/dashboard/deploy/rollback.sh

# B. Roll back to a specific older snapshot.
bash ~/actions-runners/dashboard/deploy/rollback.sh \
  --to ~/actions-runners/dashboard.bak.2026-04-29T18-00-00

# C. Verify health after rollback.
sudo systemctl is-active runner-dashboard
curl -fsS http://localhost:8321/api/health
curl -fsS http://localhost:8321/api/version
```

If rollback itself fails (no snapshots available, sync error), fall back to:

```bash
sudo systemctl stop runner-dashboard
git -C /mnt/c/Users/<you>/Repositories/runner-dashboard checkout <last-known-good-sha>
bash deploy/update-deployed.sh
```

## Resolution

After the dashboard is stable on the rolled-back version:

- Open a P1 issue with the failing log lines and the bad commit SHA.
- Revert the offending PR (`git revert`) on `main` rather than amending,
  per the project's commit policy.
- Add a regression test under `tests/api/` (backend) or `tests/frontend/`
  that fails on the bad change. Per the engineering principles in
  `CLAUDE.md`, "new feature without a test is reverted, not 'followed up'."
- Confirm the next deploy includes the test and passes CI before re-rolling
  the original feature forward.
- Audit `deploy/update-deployed.sh` to ensure it took a fresh `.bak.*`
  snapshot — if not, fix the script's snapshot logic.
- If the rollback succeeded but log rotation lost diagnostic data, raise
  retention in `deploy/scheduled-dashboard-maintenance.sh`.

## Postmortem Template

[`postmortem-template.md`](./postmortem-template.md)
