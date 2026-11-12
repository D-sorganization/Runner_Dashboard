### Fixed

- Wire the remaining `MaintenanceNotWiredError` maintenance catalogue actions (`runner_start`, `runner_stop`, `runner_restart`, `runner_drain`, `group_start`, `group_stop`, `fleet_control`, `runner_remove`, `queue_purge_stale`, `trim_worktrees`, `diagnose`) to their backend services and render `io_pressure` in the Fleet Machines table and telemetry details (#3555).
