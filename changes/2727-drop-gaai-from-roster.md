# Change fragment: drop gaai from the Runner Dashboard roster

- Removes `gaai` from `backend/coordination/roster.py`, executor coordination,
  and the vendored `agent_identity` profile (RM #2727).
- Guard test fails if `gaai` reappears in the roster or identity module.
- Worktree cleanup still handles leftover `.gaai` symlinks safely.
- Refs #2725. Refs #2727.
