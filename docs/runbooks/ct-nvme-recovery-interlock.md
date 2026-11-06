# CT-NVMe recovery interlock (#4046)

Recovery paths that stop idle runners (nightly cleanup, CT-NVMe WSL recovery,
operator drain scripts) must not interrupt a job assigned in the gap between an
idle check and `systemctl stop`.

## Contract

1. **Job hooks** — `ACTIONS_RUNNER_HOOK_JOB_STARTED` / `_COMPLETED` write and
   clear `RUNNER_BUSY_LOCK_DIR/<runner>.lock` with a live `worker_pid=`. Install
   via `deploy/install-runner-maintenance.sh`; drift is healed by
   `runner-hooks-restore` (5-minute timer).
2. **Safe stop** — Before stopping a unit, freeze it with `SIGSTOP`, re-check for
   a live Worker or busy lock, then stop. Implemented in
   `deploy/runner-scheduler.py` (`stop_runner_safely`, CLI `--stop-unit`).
   `deploy/runner-cleanup.sh` calls `/usr/local/bin/runner-scheduler --apply
   --stop-unit` instead of a bare `systemctl stop`.
3. **Defer, don't kill** — If a job appears after the freeze, thaw the unit and
   skip this pass. Python helpers share the lock rule:
   `backend.runner_recovery_interlock.should_defer_runner_stop`.

## Verify on a host

```bash
sudo /usr/local/bin/runner-hooks-restore --check
sudo /usr/local/bin/runner-scheduler --dry-run --stop-unit actions.runner.<org>-<pool>-1.service
bash deploy/deploy-check.sh --quiet   # runner_hooks check
curl -s localhost:8321/readyz | jq '.checks.runner_hooks'
```

## If a job was interrupted anyway

1. Find the workflow run in GitHub Actions and **Re-run failed jobs** (or re-run
   the workflow if the run is unusable).
2. Check `journalctl -u 'actions.runner.*'` for stops without a preceding freeze.
3. Ensure hooks and scheduler are current:
   `sudo deploy/update-deployed.sh` or wait for auto-deploy on the hub/node.

Barb attention feed for interrupted jobs is tracked separately; this runbook
covers operator recovery until that feed ships.

## Related runbooks

- [Runner corruption heal](./runner-corruption-heal.md) — break-glass `heal-host.sh`
- [Checkout not up to date](./checkout-not-uptodate.md) — hook restore and metrics
- [Fleet rollout corruption fixes](./fleet-rollout-corruption-fixes.md)
