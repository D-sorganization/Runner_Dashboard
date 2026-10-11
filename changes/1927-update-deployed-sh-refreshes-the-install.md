---
issue: 1927
summary: "update-deployed.sh refreshes the installed runner-scheduler and wsl-cache-trim assets on drift, deploy-check asserts it; scheduler drain watcher scales down under sustained load (#1929)"
dl_state: "in_review"
next_step: "After merge, run deploy/update-deployed.sh on each host to refresh the installed scheduler; re-cut RD 4.10.1 (#1834/PR #1835)."
branch: "fix/1927-scheduler-refresh-scale-down"
---
