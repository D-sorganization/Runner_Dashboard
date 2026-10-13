---
issue: 1945
summary: "Classify vanished runner toolchain as runner_state failure rather than test failure"
branch: "fix/1945-classify-missing-toolchain"
---

Classify a vanished runner toolchain (e.g. `rustc (never executed)`) as a `runner_state` failure and surface toolchain issues in the runner health check, so infrastructure corruption is not re-triaged as code failure (#1945).
