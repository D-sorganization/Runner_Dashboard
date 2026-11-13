---
issue: 2765
summary: "Share duplicate issue launch guard across issue dispatch paths"
dl_state: "in_review"
next_step: "Merge the ready PR after CI passes."
branch: "staff/issue-remediator-2765-b3264d"
---

Issue dispatches use the shared Staff duplicate guard before launching a workflow,
while the premise check continues to enforce claim ownership.
