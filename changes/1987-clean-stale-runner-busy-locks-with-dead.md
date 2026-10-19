---
issue: 1987
summary: "Clean stale runner-busy locks whose recorded Runner.Worker pid (worker_pid=, written by the job-started hook) is dead or reused; locks without worker_pid fall back to the age rule. Flag .restart-required on .env change, and add readiness probe and corruption scan metrics"
---
