---
issue: 4270
summary: "SQLite CAS stays the authoritative claim when the GitHub mirror fails, including at the RM #2539 comment cap."
dl_state: "in_review"
next_step: "Merge after exact-head PASS."
branch: "cursor-ct-2/4270-claim-cas"
---

Serving-hub `claims.claim` and `jobs.claim_job` keep the compare-and-set row when `post_agent_lease` fails. Orphan GitHub label cleanup still runs. RM #2539 at 2500 comments rolls forward without deleting history.
