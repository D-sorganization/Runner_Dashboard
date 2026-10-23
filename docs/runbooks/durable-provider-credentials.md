# Durable provider credentials (staff nodes)

Staff Hub unattended runs use **subscription** CLI auth on each node (ChatGPT, Claude Pro/Max, Google Antigravity subscription, etc.). Do not use pay-per-use API keys for fleet staff dispatch.

Monitor seats with `GET /api/staff/summary` → `provider_auth` (evening probe ~20:00 America/Los_Angeles). Statuses include `expired`, `expiring` (access token ends within 48h), and `stale` (refresh token / `auth.json` not updated in 7+ days by default — tune with `STAFF_PROVIDER_AUTH_STALE_DAYS`).

Never commit tokens. Inject long-lived values via root-only `EnvironmentFile=` (systemd) outside git.

## Claude (`claude`)

**Recommended (headless, subscription):** `claude setup-token` mints a long-lived OAuth token for the subscription seat.

1. On the node as the service user: `claude setup-token`.
2. Store the token in a root-only env file (example `/etc/runner-dashboard/claude.env`):

   ```bash
   CLAUDE_CODE_OAUTH_TOKEN=<token-from-setup-token>
   ```

3. Reference that file from the dashboard unit (`EnvironmentFile=`). Set `CLAUDE_CONFIG_DIR` if the seat is not the default `~/.claude`.

**Interactive fallback:** `claude auth login` under the same user/WSL seat the service uses.

## Codex (`codex`)

**Subscription only:** sign in with ChatGPT (`codex login`, device or browser flow). The CLI keeps a refresh token in `$CODEX_HOME/auth.json` (default `~/.codex/auth.json`) and refreshes access tokens automatically.

- Do **not** use `codex login --with-api-key` on staff nodes (pay-per-use; not fleet policy).
- Ensure `CODEX_HOME` points at the service user's directory.
- If `provider_auth` reports `stale` for codex, run `codex login` again on that node before overnight schedules.

The systemd service must allow `pid` alongside `user mnt net ipc uts` in `RestrictNamespaces`. Codex bubblewrap creates a PID namespace; a working shell `unshare -Ur true` does not prove the hardened service permits it. The shipped unit and `40-cursor-sandbox.conf` drop-in carry this allow-list while keeping cgroup namespaces restricted. No kernel sysctl change is needed for this service-only denial.

## Antigravity (`agy`)

**Subscription / Google account (CLI 1.4.5):** run `agy` interactively as the service user and select Google OAuth. Complete the browser flow and paste any authorization code only into that terminal. The CLI has no `auth login` or `auth status` subcommand; `agy models` verifies the saved login without generating a response. OAuth material lives under `ANTIGRAVITY_HOME` (default `~/.gemini`), including `oauth_creds.json`.

On WSL, install the native Linux CLI using the [official installer](https://antigravity.google/cli/install.sh) rather than linking a Windows `agy.exe`: the Windows binary can lose access to its saved login when launched in a Linux worktree. Keep the service user, HOME and ANTIGRAVITY_HOME consistent between sign-in and dispatch.

There is **no** documented Antigravity subscription path that avoids periodic Google OAuth refresh comparable to Claude `setup-token` or Codex `auth.json` auto-refresh. Do not use `GEMINI_API_KEY` / Google AI Studio keys for fleet staff (pay-per-use). Plan for re-login when `provider_auth` shows `expired`, `expiring`, or `stale`, or assign overnight work to providers with durable seats.

## Gemini CLI (`gemini`)

If used as a provider, follow that CLI's **subscription / Google account** sign-in only. Do not configure API-key billing for unattended staff runs.

## Verification

- Per-node: `GET /api/staff/board?local=1` → `provider_auth`.
- Fleet: `GET /api/staff/summary` → `provider_auth`.
- Dispatch skips `auth_expired` providers in the failover chain; `409 provider_auth_expired` only when no chain member is authenticated.
