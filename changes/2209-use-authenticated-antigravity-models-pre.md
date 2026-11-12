---
issue: 2209
summary: "Use authenticated Antigravity models preflight and allow Codex PID namespaces in hardened dashboard units"
dl_state: "in_review"
next_step: "Merge through the protected queue and deploy the provider fixes."
branch: "fix/provider-oauth-e2e-20261022"
---

hub-a dashboard smoke tests (subscription OAuth, no repository target):

- Antigravity `run-e95d1a44e39a`: succeeded, exit 0, `STAFF_RESULT: ANTIGRAVITY_E2E_OK 323`.
- Codex `run-3189019ba09c`: succeeded, exit 0, `STAFF_RESULT: CODEX_E2E_OK 323`.
- Claude `run-bce73ebbc482`: succeeded, exit 0, `STAFF_RESULT: CLAUDE_E2E_OK 323`. The tiny manual probe used the audited budget override because the seven-day plan window was above the configured ceiling; ordinary quota enforcement remains unchanged.

The Codex failure was specific to systemd's namespace allow-list, not kernel user namespace sysctls. Antigravity 1.4.5 rejects `auth status`; its authenticated `models` command works from Linux worktrees with the native binary. Regression coverage retains rejection of signed-out sessions and requires PID namespaces in node acceptance.

Repository-free dispatch and verification are already fixed on current main; this PR does not duplicate those changes. Tokens, local credential files and machine-specific launcher paths stay outside git.
