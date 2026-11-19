---
issue: 4440
summary: "Worktree sweep: private repos are never pruned or fetched, submodule worktrees are removed only after a submodule-aware check, each worktree is listed once"
paths: "backend/agent_worktree_hygiene.py, deploy/prune_agent_worktrees.py, tests/deploy/test_worktree_sweep_4440.py"
---
