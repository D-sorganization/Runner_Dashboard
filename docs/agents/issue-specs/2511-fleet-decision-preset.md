# Issue #2511 — fleet-decision panel preset (spec closure)

Pinned seat models on main (`backend/staff/panel_models.py`, preset id `fleet-decision`):

| Seat  | Provider     | Model                 |
| ----- | ------------ | --------------------- |
| Argon | antigravity  | `gemini-4-argon-high` |
| Astra | codex        | `gpt-6-astra`         |
| Fable | claude       | `claude-fable-5-1`    |

Code and docs shipped in PRs #3623 (preset/docs) and repo grounding on main. Remaining acceptance is a **live** panel run on a hub with all three providers enabled (deferred validation `DV-#2511`).
