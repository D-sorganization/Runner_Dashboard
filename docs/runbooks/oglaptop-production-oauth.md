# Production browser OAuth (fleet hub)

Human operator sign-in for the Runner Dashboard uses the **fleet GitHub App**
user-to-server OAuth flow (#2075, #2193): expiring user access tokens (~8 hours)
and rotating refresh tokens (~6 months), with refresh tokens stored encrypted on
the hub. This is **not** a separate GitHub OAuth App and **not** the
installation-token credential used by staff runs and agents.

**Fleet hub:** **DeskComputer** (`role: hub` in `backend/machine_registry.yml`).
OGLaptop is a worker node; it does not own fleet scheduling or hub-only GitHub
App private-key minting.

## MagicDNS HTTPS origins and callbacks

Tailscale **Serve** terminates TLS at the tailnet edge; the dashboard listens on
loopback `:8321`. The browser-facing origin is always **HTTPS MagicDNS without a
port**.

| Host | Role | Registry `dashboard_url` (HTTP, direct) | HTTPS origin (Serve) | OAuth callback (register on GitHub App) |
| ---- | ---- | --------------------------------------- | -------------------- | --------------------------------------- |
| **DeskComputer** | Fleet hub (production operator UI) | `http://deskcomputer.tail2bbcc7.ts.net:8321` | `https://deskcomputer.tail2bbcc7.ts.net` | `https://deskcomputer.tail2bbcc7.ts.net/api/auth/callback` |
| **OGLaptop** | Worker (not a hub origin by default) | `http://oglaptop.tail2bbcc7.ts.net:8321` | `https://oglaptop.tail2bbcc7.ts.net` | `https://oglaptop.tail2bbcc7.ts.net/api/auth/callback` |

GitHub Apps permit multiple user authorization callback URLs. Register the
callback for each HTTPS origin that is deliberately served and configured; do
not add worker callbacks by default. The hub service `EnvironmentFile` must use
one active origin/callback pair.

### Application contract (`oauth_config.py`)

The default public origin and callback come from the active registry hub. If
`DASHBOARD_PUBLIC_ORIGIN` or `GITHUB_OAUTH_CALLBACK_URL` is omitted, the runtime
derives it from that hub; if either override is supplied, the origin must be in
the active hub or explicit HTTPS allowlist and the callback must equal
`{DASHBOARD_PUBLIC_ORIGIN}/api/auth/callback`. An explicit
`DASHBOARD_OAUTH_ALLOWED_ORIGINS` entry is for a planned legacy migration only
and must be a bare HTTPS origin (no credentials, path, query, or fragment). A
worker origin such as OGLaptop is not allowed unless deliberately allowlisted.

## GitHub App settings (one-time)

In **GitHub → Organization → GitHub Apps → [fleet Runner Dashboard app]**:

1. **Expire user authorization tokens** — enable (required for `refresh_token`;
   [GitHub docs](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens)).
2. **User authorization callback URL(s)** — inspect the existing registration
   first and confirm it contains the callback for the active registry hub HTTPS
   origin (currently DeskComputer):
   `https://deskcomputer.tail2bbcc7.ts.net/api/auth/callback`. If it is absent,
   stop before attempting login and have an authorized GitHub App owner decide
   whether to add it. Add a worker callback only for an approved, deliberately
   served and allowlisted migration; do not request broader OAuth scopes.
3. **Client ID / Client secret** — use the App’s OAuth client credentials (not a
   standalone OAuth App). Generate a **new client secret** if rotating off a
   legacy OAuth App or after exposure; update only the hub `env` file on the host.
4. **Request user authorization (OAuth) during installation** — optional; hub
   login still uses **Login** → `GET /api/auth/github` on whichever HTTPS origin
   you serve.
5. **Installation private key** — hub only (`GITHUB_APP_PRIVATE_KEY_FILE`); never
   on spoke nodes. See [github-app-auth.md](github-app-auth.md).

Authorization uses scope `read:user` in the dashboard authorize URL; membership
in the `GITHUB_ORG` configured for this deployment and `config/principals.yml`
admission are both required.

## Hub environment (DeskComputer)

Paths and modes only — never commit values.

| Item | Path / rule |
| ---- | ----------- |
| Env directory | `~/.config/runner-dashboard/` mode **0700** |
| Service env file | `~/.config/runner-dashboard/env` mode **0600**, referenced by `EnvironmentFile=` in `runner-dashboard.service` |
| App PEM (agents) | `~/.config/runner-dashboard/github-app.private-key.pem` mode **0600** |
| OAuth token store (runtime) | `~/.config/runner-dashboard/github_user_oauth.json` mode **0600** (override: `DASHBOARD_GITHUB_USER_OAUTH_STORE`) |

Required keys for browser OAuth (names only):

```dotenv
GITHUB_CLIENT_ID=<fleet-github-app-client-id>
GITHUB_CLIENT_SECRET=<fleet-github-app-client-secret>
GITHUB_ORG=your-github-organization
DASHBOARD_TLS=1
SESSION_SECRET=<at-least-32-chars>
```

`DASHBOARD_PUBLIC_ORIGIN` and `GITHUB_OAUTH_CALLBACK_URL` are optional when
the runtime can load the fleet registry. When set explicitly, use the active
hub HTTPS origin and its `/api/auth/callback` URL, and verify that callback is
registered on the existing fleet GitHub App before attempting login.

Hub installation credentials (same file; not copied to nodes):

```dotenv
GITHUB_APP_ID=<app-id>
GITHUB_APP_INSTALLATION_ID=<installation-id>
GITHUB_APP_PRIVATE_KEY_FILE=/home/<user>/.config/runner-dashboard/github-app.private-key.pem
```

Omit entirely (do not set to `0`): `DASHBOARD_DEV_LOGIN`, `DASHBOARD_LOOPBACK_AUTH`,
`DASHBOARD_LOOPBACK_OPERATOR_WRITES`. Do not set `STAFF_ALLOW_OPERATOR_TOKEN=1`
on spoke nodes.

## Transport

- Bind dashboard to loopback; expose **Tailscale Serve**, not Funnel.
- Verify `tailscale serve status` before restart; `tailscale funnel status` must
  show no public dashboard mapping.
- Session cookie: `Secure`, `HttpOnly`, `SameSite=Strict` when `DASHBOARD_TLS=1`.

## Current Hub Snapshot (Checked 2026-10-23)

The active DeskComputer deployment reports a healthy dashboard and the current
Tailscale Serve route is `https://deskcomputer.tail2bbcc7.ts.net` proxying to the
local dashboard on port 8321. OAuth is separately blocked: `/api/health` reports
`github_client_id_invalid`, `github_client_secret_invalid`,
`session_secret_invalid`, `tls_required`, and
`development_auth_must_be_unset`. The service process has no client ID, client
secret, session secret, or `DASHBOARD_TLS` setting; it does have
`DASHBOARD_LOOPBACK_AUTH` and `DASHBOARD_LOOPBACK_OPERATOR_WRITES`. Values are
not recorded here. The origin and callback overrides are unset, so the runtime
uses the registry-derived DeskComputer origin and callback. Recheck this snapshot
with `/api/health` and `tailscale serve status` before any setup; a healthy route
does not mean OAuth credentials are ready. The existing App callback registration
has not been verified.

## Verification

After the authorized hub environment setup and a normal deploy:

1. `GET https://<your-active-origin>/api/health` — `oauth.ready: true`,
   `oauth.flow: "github_app_user_to_server"`, `oauth.operator_sessions` present
   after login (no token strings).
2. Tailnet browser: **Login** → GitHub → callback → `GET /api/auth/me` shows a
   `principals.yml` operator (not `__loopback__`).
3. `GET /api/staff/summary` — `operator_auth.operators[]` with expiry fields for Barb.
4. Save a directive/hold — no 503 “Production GitHub OAuth is not ready.”
5. `POST /api/auth/logout` clears session and hub OAuth store row for that principal.

## Safe diagnostics

`/api/health` → `oauth` reports `ready`, `status`, `reason`, and `flow` only, plus
redacted `operator_sessions`. Reason codes (no secret values):

| Reason | Operator action |
| ------ | ----------------- |
| `github_client_id_invalid` | Set fleet App Client ID on hub. |
| `github_client_secret_invalid` | Set or rotate App client secret on hub. |
| `github_org_missing` | Set `GITHUB_ORG` to the organization whose members may sign in. |
| `github_org_invalid` | Use a valid GitHub organization name in `GITHUB_ORG`. |
| `public_origin_mismatch` | Use an active registry hub HTTPS origin or add a planned HTTPS origin to the explicit allowlist. |
| `callback_url_mismatch` | Set the callback to `{origin}/api/auth/callback` and register it in GitHub App settings. |
| `session_secret_invalid` | Explicit `SESSION_SECRET` ≥32 chars. |
| `tls_required` | `DASHBOARD_TLS=1`. |
| `development_auth_must_be_unset` | Remove dev/loopback auth env keys. |

## Rotation and rollback

Rotate **client secret** in GitHub App settings, update hub `env`, restart service,
verify login, revoke old secret. Rotating `SESSION_SECRET` invalidates browser
sessions. On failure, restore previous `env` and release; do not enable dev login,
loopback auth, or Funnel as a workaround.
