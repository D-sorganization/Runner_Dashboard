---
issue: 2931
summary: "Persist consecutive RM refresh skips and raise dashboard attention after three"
---

Three consecutive skipped RM refreshes set attention=true; updated and unchanged refreshes clear the counter and attention. Validate via the auto-deploy timer after merge.
