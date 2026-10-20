---
issue: 2002
summary: "runner-cleanup reaps idle leaked ephemeral buildx builders (builder-*, gm-ci-*) older than 3h with no active build; never ud-buildkit"
dl_state: "in_review"
next_step: "Merge the PR, then redeploy runner-cleanup via install-storage-gc.sh on each host."
branch: "feat/idle-builder-reaper-2002"
---
