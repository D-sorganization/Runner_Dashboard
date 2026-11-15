---
issue: 4249
summary: "automerge_guard holds a PR after a disarm by any account, bots included, except the Merge-Hold-Guard workflow and disarms the guard marks as its own with a PR comment; the merge shepherd counts merge-queue ejections of the current head and stops arming at the bounce limit of 2; a drain gate for the shepherd ships off by default behind MERGE_SHEPHERD_DRAIN_GATE"
---
