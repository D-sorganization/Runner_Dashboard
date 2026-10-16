---
issue: 1945
summary: "fix(remediation): classify a vanished runner toolchain as a runner_state failure and surface it in the runner health check."
---

Classify a vanished runner toolchain (e.g. `rustc (never executed)`) as a `runner_state` failure and surface toolchain issues in the runner health check, so infrastructure corruption is not re-triaged as a code failure (#1945).
