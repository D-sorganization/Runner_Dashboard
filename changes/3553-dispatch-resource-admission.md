---
area: staff
kind: fix
issues: [3553]
---

Enforce configurable CPU, RAM, and PSI I/O-pressure admission thresholds on local `dispatch_staff_run` calls before admitting or launching a worker, and make `runner_autoscaler` I/O-pressure poll-loop tests hermetic when `runner_lease` / FastAPI imports or ambient thresholds vary (#3553).
