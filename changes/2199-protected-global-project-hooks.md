---
issue: 2199
summary: "Delegate project quality hooks to the protected global runtime."
dl_state: "in_review"
next_step: "Merge and qualify the protected global hook rollout."
branch: "fix/protected-global-project-hooks-2199"
---

## Changed

- Retire exact legacy project quality callbacks in favor of the administrator-owned global hooks for Claude, Codex, Cursor, AGY and Gemini.
- Preserve unrelated settings/callbacks, Cursor agent-bus session startup and Git pre-push checks; keep generated adapters idempotent and drift-checked.

## Validation

- Regression coverage removes legacy commands while preserving unrelated callbacks/settings for all five provider formats.
- Host global hook activation is qualified separately; Codex still requires user review of the exact global hook definitions.
