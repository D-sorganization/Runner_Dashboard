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
| **OGLaptop** | Worker (legacy #1141 pin in code; see below) | `http://oglaptop.tail2bbcc7.ts.net:8321` | `https://oglaptop.tail2bbcc7.ts.net` | `https://oglaptop.tail2bbcc7.ts.net/api/auth/callback` |

GitHub Apps allow **multiple user authorization callback URLs**. Register **both**
callbacks on the fleet App so either hostname can complete login during migration
or if Serve is enabled on more than one node. The hub service `EnvironmentFile`
must still match the **application contract** in the next section (only one origin
pair is active per process).

### Application contract (`oauth_config.py`)

Until a follow-up code change updates the pinned origin, production readiness
checks require **exactly** these env values (issue #1141 / `SPEC.md`):

| Variable | Required value |
| -------- | -------------- |
| `DASHBOARD_PUBLIC_ORIGIN` | `https://oglaptop.tail2bbcc7.ts.net` |
| `GITHUB_OAUTH_CALLBACK_URL` | `https://oglaptop.tail2bbcc7.ts.net/api/auth/callback` |

If DeskComputer is the live hub but env is set to the DeskComputer HTTPS origin,
`/api/health` reports `public_origin_mismatch` / `callback_url_mismatch` and
operator writes return 503. **Operational alignment:** either (a) point
`DASHBOARD_PUBLIC_ORIGIN` / `GITHUB_OAUTH_CALLBACK_URL` at the pinned OGLaptop
HTTPS origin while Serve on DeskComputer also answers that hostname, or (b) land
a small code change to pin DeskComputer (or configurable origin) and then set hub
env to the DeskComputer row above.

## GitHub App settings (one-time)

In **GitHub → Organization → GitHub Apps → [fleet Runner Dashboard app]**:

1. **Expire user authorization tokens** — enable (required for `refresh_token`;
   [GitHub docs](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens)).
2. **User authorization callback URL(s)** — add both:
   - `https://deskcomputer.tail2bbcc7.ts.net/api/auth/callback`
   - `https://oglaptop.tail2bbcc7.ts.net/api/auth/callback`
3. **Client ID / Client secret** — use the App’s OAuth client credentials (not a
   standalone OAuth App). Generate a **new client secret** if rotating off a
   legacy OAuth App or after exposure; update only the hub `env` file on the host.
4. **Request user authorization (OAuth) during installation** — optional; hub
   login still uses **Login** → `GET /api/auth/github` on whichever HTTPS origin
   you serve.
5. **Installation private key** — hub only (`GITHUB_APP_PRIVATE_KEY_FILE`); never
   on spoke nodes. See [github-app-auth.md](github-app-auth.md).

Authorization uses scope `read:user` in the dashboard authorize URL; org membership
(`D-sorganization`) and `config/principals.yml` admission are unchanged.

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
GITHUB_ORG=D-sorganization
DASHBOARD_PUBLIC_ORIGIN=<must-match-oauth_config-pin-see-table-above>
GITHUB_OAUTH_CALLBACK_URL=<same-origin>/api/auth/callback
DASHBOARD_TLS=1
SESSION_SECRET=<at-least-32-chars>
```

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

## Verification

After hub env is provisioned and `main` has deployed (#2193+):

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
| `github_org_mismatch` | `GITHUB_ORG=D-sorganization`. |
| `public_origin_mismatch` | Match `DASHBOARD_PUBLIC_ORIGIN` to `oauth_config` pin or update code pin. |
| `callback_url_mismatch` | Match callback to pin + registered GitHub App URL. |
| `session_secret_invalid` | Explicit `SESSION_SECRET` ≥32 chars. |
| `tls_required` | `DASHBOARD_TLS=1`. |
| `development_auth_must_be_unset` | Remove dev/loopback auth env keys. |

## Rotation and rollback

Rotate **client secret** in GitHub App settings, update hub `env`, restart service,
verify login, revoke old secret. Rotating `SESSION_SECRET` invalidates browser
sessions. On failure, restore previous `env` and release; do not enable dev login,
loopback auth, or Funnel as a workaround.
