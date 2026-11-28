---
issue: 4702
summary: "Cache and budget-bound the job-board open-issues listing so GET /api/coordination/jobs stops shelling out to GitHub on every request and degrades to the last-known list instead of a bare 503."
---
