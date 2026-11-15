---
issue: 4249
summary: "automerge_guard bounds every gh call with a timeout (120 s, AUTOMERGE_GUARD_GH_TIMEOUT_SECONDS), refuses to arm when the armed-PR cap policy is unreadable or an enforced cap's store fails, releases the cap reservation when the arm raises, and returns the hold reasons to the merge shepherd pass, which records them per PR"
---
