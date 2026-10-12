# Fleet maintenance: drain, reboot and resume (#1936)

`POST /api/fleet/maintenance` (scope `fleet.control`) drains or resumes the
runners on the machine the dashboard runs on. It is privileged: with
`reboot: true` it reboots the host once the drain completes.

| Body                                  | Effect                                                                        |
| ------------------------------------- | ----------------------------------------------------------------------------- |
| `{"action": "drain"}`                 | Hold at 0 runners, let busy jobs finish (up to an hour). Returns immediately. |
| `{"action": "drain", "reboot": true}` | As above, then reboot the host.                                               |
| `{"action": "resume"}`                | Clear the maintenance hold. Waits for the (fast) command.                     |

Anything else is a `400` and nothing is executed: non-object body, malformed
JSON, missing/unknown/non-string `action`, non-boolean `reboot`, `reboot` with
`resume`, unknown fields. A missing scheduler binary is `503`; a failing command
is `500`. Progress is `state.maintenance` (`active`, `drained`, `rebooting`) in
`GET /api/fleet/schedule`; the dashboard Runner Schedule tab shows it.

## Command built

The argv is a list (no shell) from the validated enum only; the scheduler binary
and the three paths come from server config (`RUNNER_SCHEDULER_BIN`,
`RUNNER_ROOT`/runner base dir, `RUNNER_SCHEDULE_CONFIG`, `RUNNER_SCHEDULER_STATE`).

```
sudo -n systemd-run --unit=runner-maintenance-drain --collect \
  --setenv=RUNNER_ROOT=<root> --setenv=RUNNER_SCHEDULE_CONFIG=<config> \
  --setenv=RUNNER_SCHEDULER_STATE=<state> <runner-scheduler> --apply --drain [--reboot]

sudo -n systemd-run --wait --collect <same --setenv args> <runner-scheduler> --apply --resume
```

## Sudoers scope

`systemd-run` as root can run anything, so the grant MUST be the three exact
command lines (no `*` wildcards), never `systemd-run` in general. Substitute the
real paths, then validate with `visudo -cf`:

```
# /etc/sudoers.d/runner-dashboard-maintenance   (mode 0440)
Cmnd_Alias RUNNER_MAINT = \
  /usr/bin/systemd-run --unit=runner-maintenance-drain --collect --setenv=RUNNER_ROOT=<root> --setenv=RUNNER_SCHEDULE_CONFIG=<config> --setenv=RUNNER_SCHEDULER_STATE=<state> /usr/local/bin/runner-scheduler --apply --drain, \
  /usr/bin/systemd-run --unit=runner-maintenance-drain --collect --setenv=RUNNER_ROOT=<root> --setenv=RUNNER_SCHEDULE_CONFIG=<config> --setenv=RUNNER_SCHEDULER_STATE=<state> /usr/local/bin/runner-scheduler --apply --drain --reboot, \
  /usr/bin/systemd-run --wait --collect --setenv=RUNNER_ROOT=<root> --setenv=RUNNER_SCHEDULE_CONFIG=<config> --setenv=RUNNER_SCHEDULER_STATE=<state> /usr/local/bin/runner-scheduler --apply --resume
<dashboard-user> ALL=(root) NOPASSWD: RUNNER_MAINT
```

Without this file the endpoint returns `500` (`sudo: a password is required`)
and nothing runs. Keep the scheduler binary root-owned and not writable by the
dashboard user, or the grant is equivalent to root.

## Audit

Each accepted request writes two orchestration audit entries
(`orchestration_type: fleet_maintenance`, `action: fleet.maintenance.<action>`,
`requested_by: <principal id>`): `phase: requested` before the command runs and
`phase: completed` with `outcome: started|failed` after. Rejected (4xx) requests
write none.
