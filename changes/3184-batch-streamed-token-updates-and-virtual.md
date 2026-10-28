---
issue: 3184
summary: "Batch streamed token updates and virtualize long thread histories"
dl_state: "in_review"
next_step: "Run browser event-to-paint p95 on a supported environment."
branch: "codex/3184-stream-frame-batching"
---

The browser event-to-paint p95 was not measured locally. The required Playwright
CLI could not start because this environment's Node runtime reports that WSL 1
is unsupported. The PR records this limitation instead of presenting a
synthetic frame time as a device measurement.
