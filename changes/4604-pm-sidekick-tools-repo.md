---
issue: 4604
summary: "fix(staff): generate the pm-sidekick role for the Tools repository"
---

The scheduled role listed a nonexistent Sidekick repository, so the run failed while cloning. The generated role now targets Tools, stays report-only, and still states that the Sidekick packaging hold is separate.
