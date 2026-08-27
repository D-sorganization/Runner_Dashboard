# OGLaptop Production Browser OAuth

This runbook provisions the dedicated GitHub OAuth App used by human operators
at the tailnet-only Runner Dashboard origin. It does not change the GitHub App
used for backend API calls, and it does not authorize a personal access token,
remote development login, Tailscale Funnel, or extraction of an existing App
secret.

## Fixed contract

| Setting                  | Required value or rule                                                      |
| ------------------------ | --------------------------------------------------------------------------- |
| Homepage / public origin | `https://oglaptop.tail2bbcc7.ts.net`                                        |
| Authorization callback   | `https://oglaptop.tail2bbcc7.ts.net/api/auth/callback`                      |
| OAuth scope              | `read:user`                                                                 |
| Organization admission   | Exact `D-sorganization` membership, then an existing `principals.yml` match |
| Transport                | Tailscale Serve over HTTPS, tailnet-only                                    |
| Session cookie           | `Secure`, `HttpOnly`, `SameSite=Strict`                                     |

Raw IPs, alternate Host headers, LAN names, HTTP URLs, callback wildcards, and
public Funnel exposure are outside the contract. The configured canonical
origin is authoritative; forwarded Host and protocol headers do not alter it.

## One-time administrator provisioning

1. In the reviewed GitHub organization or administrator account, create a
   dedicated OAuth App for Runner Dashboard operators. Register the exact
   homepage and callback above. Do not reuse the backend GitHub App secret.
2. Create or select a root/service-owned environment directory with mode
   `0700`, and a service `EnvironmentFile` with mode `0600`. The file must
   contain the following keys; substitute credential values only on the host:

   ```dotenv
   GITHUB_CLIENT_ID=<dedicated-oauth-client-id>
   GITHUB_CLIENT_SECRET=<dedicated-oauth-client-secret>
   GITHUB_ORG=D-sorganization
   DASHBOARD_PUBLIC_ORIGIN=https://oglaptop.tail2bbcc7.ts.net
   GITHUB_OAUTH_CALLBACK_URL=https://oglaptop.tail2bbcc7.ts.net/api/auth/callback
   DASHBOARD_TLS=1
   SESSION_SECRET=<independent-random-value-at-least-32-characters>
   ```

   Omit `DASHBOARD_DEV_LOGIN` and `DASHBOARD_LOOPBACK_AUTH` completely. Do not
   write either key as `0`; production readiness requires that neither variable
   exist in the service environment.

3. Reference the protected file from the service unit. Never place a secret in
   unit text, repository files, shell history, workflow inputs, logs,
   diagnostics, screenshots, or artifacts.
4. Keep the dashboard backend bound to loopback and configure **Tailscale
   Serve**, not Funnel, for the fixed HTTPS origin. Before any approved restart,
   verify `tailscale serve status` shows the intended local backend and
   `tailscale funnel status` shows no public dashboard mapping. Tailnet ACLs
   remain the first admission boundary.
5. Apply the service change only during an approved deployment window. This
   repository change does not authorize a restart or deployment.

## Verification

After the administrator has provisioned the secret and an approved deployment
has completed:

1. Read `/api/health` through the MagicDNS HTTPS origin. The only OAuth fields
   must be `ready`, `status`, and `reason`, with values
   `true`, `ready`, and `configured`.
2. Confirm the session cookie is `Secure`, `HttpOnly`, and `SameSite=Strict`.
3. In a private browser window connected to the tailnet, select **Login**. The
   GitHub authorization request must show only `read:user` and return to the
   exact callback above.
4. Confirm a non-member and a member absent from `principals.yml` are both
   denied. Confirm logout revokes the active dashboard session.
5. Confirm a raw Tailscale IP or alternate hostname cannot become an OAuth
   callback. Do not weaken the registered callback to make an alias work.

## Safe diagnostics

`/api/health` reports semicolon-delimited reason codes without values:

| Reason                           | Operator action                                                           |
| -------------------------------- | ------------------------------------------------------------------------- |
| `github_client_id_invalid`       | Install the dedicated OAuth client ID; do not log it.                     |
| `github_client_secret_invalid`   | Install or rotate the dedicated OAuth client secret.                      |
| `github_org_mismatch`            | Restore the exact organization admission policy.                          |
| `public_origin_mismatch`         | Restore the fixed MagicDNS HTTPS origin.                                  |
| `callback_url_mismatch`          | Restore the exact registered callback.                                    |
| `session_secret_invalid`         | Install an independent explicit session secret of at least 32 characters. |
| `tls_required`                   | Enable TLS mode before admitting browser sessions.                        |
| `development_auth_must_be_unset` | Remove both development-auth variables from the service environment.      |

Diagnostics must never include client IDs, client secrets, OAuth state, access
tokens, session identifiers, or session secrets.

## Rotation and rollback

For OAuth secret rotation, create the replacement through GitHub's OAuth App
administration, update only the protected EnvironmentFile, use the approved
deployment/restart process, complete browser verification, then revoke the old
secret. For session-secret rotation, expect every browser session to be
invalidated and require a fresh login; coordinate this as a user-visible
maintenance event.

To roll back a failed enablement, restore the previous protected file and the
previous qualified release through the deployment rollback procedure. If no
known-good browser credential exists, remove the OAuth credential keys and
leave the dashboard fail closed; do not enable development login, loopback
auth, a broad PAT, or Funnel as a workaround.
