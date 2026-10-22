# Durable provider credentials (staff nodes)

Staff Hub unattended runs depend on **node-local** CLI auth. Interactive OAuth sessions expire overnight; plan for long-lived or renewable credentials and monitor `GET /api/staff/summary` → `provider_auth` (evening probe ~20:00 America/Los_Angeles).

Never commit tokens or API keys. Use root-only env files or systemd `EnvironmentFile=` entries outside git.

## Claude (`claude`)

**Recommended (headless):** long-lived setup token in the service environment.

1. On the node (once, as the operator): `claude setup-token` and copy the token.
2. Store it in a root-only file (example `/etc/runner-dashboard/claude.env`):

   ```bash
   CLAUDE_CODE_OAUTH_TOKEN=<token-from-setup-token>
   ```

3. Point the dashboard unit at that file (`EnvironmentFile=`). The staff child inherits `CLAUDE_CONFIG_DIR` when configured; token env is read by Claude Code CLI.

**Interactive fallback:** `claude auth login` under the same user/WSL seat the service uses. Expiry is visible in `~/.config/runner-dashboard/claude/.credentials.json` or `~/.claude/.credentials.json`.

## Codex (`codex`)

**Recommended:** API key login (non-interactive).

```bash
codex login --with-api-key
```

Persist `CODEX_HOME` (default `~/.codex`) for the service user. Session material lives in `$CODEX_HOME/auth.json`.

## Antigravity (`agy`)

**Interactive default:** `agy auth login` (browser). OAuth files under `ANTIGRAVITY_HOME` (default `~/.gemini`), including `oauth_creds.json`. These seats expire like other Google OAuth flows.

**Headless / long-lived option (Gemini API key):** Antigravity CLI 1.1.13+ supports direct Gemini API access without a signed-in Google account when configured explicitly:

1. Set `modelProvider` to `gemini` in `~/.gemini/antigravity-cli/settings.json` ([Antigravity auth docs](https://antigravity.google/docs/install/)).
2. Export `GEMINI_API_KEY` in the **service** environment (not `.env` files the CLI ignores). See [headless mode](https://antigravity.google/docs/cli/headless/).

`GOOGLE_API_KEY` and `.env` alone are **not** read for this path. `/logout` does not clear an API-key seat.

If neither OAuth nor `GEMINI_API_KEY` is configured, overnight scheduled runs fail with `auth_expired` until login is refreshed.

## Gemini CLI (`gemini`)

Use the provider's documented API-key or login flow for the installed `gemini` binary on the node. For fleet staff, prefer `agy` with `GEMINI_API_KEY` above when Antigravity is the Gemini surface.

## Verification

- Per-node: `GET /api/staff/board?local=1` → `provider_auth`.
- Fleet brief: `GET /api/staff/summary` → `provider_auth` (aggregated across online peers).
- Dispatch preflight: expired OAuth returns `409` with `provider_auth_expired` only when **no** provider in the role failover chain is authenticated.
