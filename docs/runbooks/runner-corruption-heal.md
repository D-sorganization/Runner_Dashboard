# Runbook: heal a host hit by runner corruption

## Symptom

CI on this host's runners stops passing. Common signatures (all from
issue [#651](https://github.com/D-sorganization/Runner_Dashboard/issues/651)):

- Job log: `Missing file at path: .../_runner_file_commands/save_state_<uuid>`
- Job log: `The file '.../_diag/pages/<uuid>_<uuid>_1.log' already exists`
- `journalctl -u 'actions.runner.*'` shows
  `Unit process X (Runner.Worker) remains running after unit stopped`
- `gh api orgs/<org>/actions/runners` shows runners stuck `busy:true`
  for hours with no matching `Runner.Worker` process on the host.

## Diagnose (60 seconds)

```bash
# Are the symptoms present?
sudo journalctl -u 'actions.runner.*' --since '1 hour ago' \
    | grep -E 'remains running after unit stopped|_runner_file_commands|already exists' \
    | tail -20

# Stale corruption on the filesystem?
for d in /home/*/actions-runners/runner-*; do
    fc="$d/_work/_temp/_runner_file_commands"
    [[ -d "$fc" ]] && echo "STALE file_commands: $(basename "$d") $(ls "$fc" | wc -l) files"
    p="$d/_diag/pages"
    [[ -d "$p" ]] && n=$(find "$p" -name '*.log' -mtime +1 | wc -l) && (( n > 0 )) \
        && echo "STALE diag/pages: $(basename "$d") $n old log(s)"
done
```

If anything prints, this host is corrupted; continue to **Heal**.

## Heal

```bash
# One command. Drains every actions.runner.* unit, kills orphan
# Workers/Listeners, runs the canonical cleanup, restarts everyone.
sudo /opt/runner-dashboard/deploy/heal-host.sh
```

Exit code 0 means every unit came back active. Exit code 1 means at
least one unit failed to restart — `systemctl status <unit>` and
`journalctl -u <unit> -n 50` will name the failure.

For a dry-run first: `sudo /opt/runner-dashboard/deploy/heal-host.sh --dry-run`.

## After healing

1. Confirm the host is accepting jobs: watch `journalctl -u 'actions.runner.*'` for the next `Listening for Jobs` line on each unit.
2. Re-run any cancelled or failed workflows via the dashboard's Workflows tab, or:
   ```bash
   gh api -X POST "repos/<org>/<repo>/actions/runs/<run_id>/rerun"
   ```
3. If the host re-corrupts within 24 hours, the autoscaler kill-window race is firing on this host. Escalate to the issue tracking the autoscaler race fix and consider stopping the autoscaler on this host until it lands:
   ```bash
   sudo systemctl stop runner-autoscaler.service
   ```

## Why this happens

See [`docs/observability.md`](../observability.md) for the three failure
modes that converge into "host won't run jobs":

1. Autoscaler kills runner in the brief window between job pickup
   and `Runner.Worker` fork → leaves `_runner_file_commands` residue.
2. Runner systemd unit uses `KillMode=process`, so when the listener
   exits, the Worker child orphans → leaves `_diag/pages` log handles
   open.
3. Nightly cleanup `runner-cleanup.service` was supposed to mop these
   up, but a strict-mode regression made it abort on the first
   stop-failure every night (fixed in
   [#660](https://github.com/D-sorganization/Runner_Dashboard/pull/660)).

`heal-host.sh` is the operator's break-glass tool until #3 and the
autoscaler race fix land fleet-wide.

## Related

- Cleanup script: [`deploy/runner-cleanup.sh`](../../deploy/runner-cleanup.sh)
- Cleanup strict-mode fix: [#660](https://github.com/D-sorganization/Runner_Dashboard/pull/660)
- Original corruption report: [#651](https://github.com/D-sorganization/Runner_Dashboard/issues/651)
- Autoscaler context: [#640](https://github.com/D-sorganization/Runner_Dashboard/issues/640)
