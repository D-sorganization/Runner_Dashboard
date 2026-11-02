# Stale Antigravity (agy) login

Staff nodes use the **Antigravity CLI** with a Google subscription account. OAuth state lives under `ANTIGRAVITY_HOME` (default `~/.gemini`), mirrored between WSL and Windows by `deploy/sync_provider_credentials.py` on each self-heal cycle.

## Detection (hub / dashboard)

1. **Staff summary attention** — `GET /api/staff/summary` → `attention[]` with:
   - `failure_class`: `auth_expired`, `auth_expiring`, or `auth_stale` (evening OAuth preflight, #2170)
   - `failure_class`: `credential_mirror_stale` when a WSL/Windows **copy** of agy creds lags the winning source (`provider_auth.credential_mirrors` on each machine)
2. **Per-node board** — `GET /api/staff/board?local=1` → `provider_auth.providers.antigravity.status` and `provider_auth.credential_mirrors.providers.antigravity`
3. **CLI probe (on the node)** — as the service user, `agy models` must list models without an auth error (there is no `agy auth status` subcommand)
4. **Credentials page** — Antigravity probe shows `authenticated: false` or quota detail when the seat is dead

Typical stale patterns:

- Windows `agy.exe` was used to sign in but dispatch runs the **Linux** `agy` in WSL (split-brain; see [durable-provider-credentials.md](durable-provider-credentials.md))
- Self-heal could not write a mirror (Windows ACL) — mirror health flags `older_than_source` or `content_drift` while one side still looks “fine” locally
- Google refresh expired — `provider_auth` shows `expired` or `stale` (>7 days without refresh by default)

## Remediation

1. Confirm which side has the freshest login (newer file mtime under `~/.gemini/antigravity-cli/` vs `/mnt/c/Users/<you>/.gemini/antigravity-cli/`).
2. On the **Linux seat the dashboard uses**, run interactive `agy` and complete Google OAuth (browser + paste code only in that terminal).
3. Run credential sync once (no secret output):

   ```bash
   python3 deploy/sync_provider_credentials.py
   # or wait for runner-dashboard-self-heal.timer
   ```

4. Re-check `GET /api/staff/summary` — mirror attention should clear when all copies match the source.
5. If dispatch still fails, verify `ANTIGRAVITY_HOME` and `HOME` match between sign-in and the systemd unit.

Do **not** put `GEMINI_API_KEY` / AI Studio keys on staff nodes (pay-per-use; fleet policy).

## Related

- [Durable provider credentials](durable-provider-credentials.md)
- [barb-worker VM](../agents/barb-worker-vm.md) (Barb lanes use hub tokens, not agy seats on the worker)
