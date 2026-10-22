# Required TypeScript typecheck and detect-secrets on main

- Policy, workflow, and repository ruleset supplement to require `TypeScript typecheck (tsc)` and `detect-secrets (baseline diff)` on `main`.
- `workflow_dispatch` apply workflow uses `BRANCH_PROTECTION_TOKEN` after merge.
