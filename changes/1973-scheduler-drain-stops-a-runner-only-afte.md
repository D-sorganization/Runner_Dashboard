---
issue: 1973
summary: "Scheduler drain stops a runner only after it stays between jobs for RUNNER_DRAIN_SETTLE_SECONDS (30 s), so it no longer orphans the job GitHub assigns a just-freed runner; a slow systemctl stop no longer crashes the run"
---
