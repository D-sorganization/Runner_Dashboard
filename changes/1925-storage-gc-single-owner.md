---
issue: 1925
summary: "install-storage-gc.sh becomes the single owner of runner-cleanup + disk-guard units with a --verify contract; fleet_storage_gc.py rolls it out over Tailscale; fixes disk-guard abort under /tmp pressure; workstation profile."
dl_state: "in_review"
next_step: "brick needs an interactive sudo install (command printed by fleet_storage_gc.py); RM counterpart retires the forked installers."
title: "Fleet Storage GC: Single Owner, Verify Contract, Tailscale Rollout"
branch: "fix/1925-storage-gc-drift"
---
