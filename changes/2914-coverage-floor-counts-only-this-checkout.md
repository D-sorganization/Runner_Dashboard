---
issue: 2914
summary: "Coverage floor counts only this checkout's backend/ ([tool.coverage.report] include), so a child process that resolves --cov=backend against another checkout no longer drags the total below the floor."
---
