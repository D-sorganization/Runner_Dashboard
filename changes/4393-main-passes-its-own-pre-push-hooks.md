---
issue: 4393
summary: "Main passes its own pre-push hooks: repair 78 invalid change fragments, name the staff.playbooks exports instead of wildcard imports, apply prettier to 90 docs and config files, keep recorded test fixtures out of prettier, add a tree-wide fragment validation test, and count changes/ fragments in the anti-phantom path extractor"
---

The anti-phantom workflow checks out `scripts/phantom_guard_issue_paths.py` from the pull request base ref. #4672 put that `changes/` extractor on main. This commit re-runs Rule 3 against it. The issue body names `changes/4249-merge-shepherd-exact-head-pass-eligibili.md` and `changes/4270-claim-state-cas-acquire.md`, and this pull request already touches both.
