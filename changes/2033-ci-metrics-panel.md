---
issue: 2033
summary: "Add an Operations CI job metrics panel with queue and execution percentiles and a conservative provisional queue SLO bound"
dl_state: "in_review"
next_step: "Parent review and publish after resolving the pre_pr entry-point gap; no push or PR opened."
branch: "codex/2033-ci-metrics-panel"
paths: "frontend/src/pages/Operations,frontend/src/pages/Operations/__tests__,changes"
---

Validation: focused Vitest 10 passed; TypeScript typecheck passed; ESLint passed. Prettier applied. scripts.pre_pr is absent from this checkout, so the required module command could not run. Rollback: build with VITE_CI_METRICS_PANEL_ENABLED=false.
