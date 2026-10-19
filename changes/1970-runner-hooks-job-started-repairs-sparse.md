---
issue: 1970
summary: "Runner hooks: job-started repairs sparse residue in place, quarantines partial clones with missing blobs and reseeds them from a healthy local sibling, removes stale workspace locks; runner-hooks-restore self-heals unit wiring and .env hook overrides and never downgrades installed hooks; refresh-maintenance-assets refreshes hooks; job-completed clears _work/_temp; scheduled gitconfig token-rewrite cleanup"
dl_state: "in_review"
next_step: "Review and merge the PR, then run update-deployed.sh on fleet hosts and check runner-hooks-restore --check."
branch: "fix/runner-hooks-workspace-quarantine"
---
