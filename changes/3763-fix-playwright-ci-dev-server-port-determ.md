---
issue: 3763
summary: "fix Playwright CI dev-server port determinism: cache the resolved port to a job-scoped lock file so repeated worker-process re-imports of playwright.config.ts read back the same port instead of each probing a new one"
---
