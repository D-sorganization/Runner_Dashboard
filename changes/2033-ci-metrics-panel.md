---
issue: 2033
summary: "Add an Operations CI job metrics panel with queue and execution percentiles and a conservative provisional queue SLO bound"
dl_state: "in_review"
next_step: "Publish the reviewed branch when GitHub network policy permits; no push or PR opened."
branch: "codex/2033-ci-metrics-panel"
paths: "frontend/src/pages/Operations,frontend/src/pages/Operations/__tests__,changes"
---

Validation: focused Vitest 10 passed; TypeScript typecheck passed; ESLint passed. Prettier applied. The checkout lacks scripts.pre_pr; the canonical Repository_Management shared runner at b8310d28 passed all eight gates against this checkout with networking disabled (inapplicable Python, workflow and absent fleet-script checks were skipped). Rollback: build with VITE_CI_METRICS_PANEL_ENABLED=false.
