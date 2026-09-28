# Phone Access & Web Push over Private Tailnet — Runbook

This runbook guides operators through setting up secure, zero-cloud phone access
(iOS / Android PWA) and end-to-end Web Push notifications for the Runner Dashboard
over a private Tailscale network.

---

## 1. Architectural Principles & Policy

- **ADR-0007 Compliance**: Tailscale **Funnel is strictly forbidden**. The dashboard
  origin and Web Push notifications must remain strictly private to the operator's
  tailnet. No public ports or external proxy tunnels are permitted.
- **Transport Security**: All traffic is routed over Tailscale WireGuard and secured
  with TLS via `tailscale serve`.
- **Zero Third-Party Relays**: Web Push uses RFC 8291 (`aes128gcm` payload encryption)
  and RFC 8292 (VAPID JWT with ES256) directly from the backend using Python's standard
  libraries and the pinned `cryptography` package. No paid push services or third-party
  APIs are required.
- **iOS PWA Requirement**: On iOS (16.4+), Apple Web Push requires the web application
  to be added to the Home Screen ("Add to Home Screen") to receive push notifications.

---

## 2. Fixed Topology & Contract

| Component          | Target / Value                                                  | Notes                                                                         |
| :----------------- | :-------------------------------------------------------------- | :---------------------------------------------------------------------------- |
| **Tailnet Origin** | `https://<node-name>.<tailnet>.ts.net`                          | Canonical HTTPS URL assigned by Tailscale MagicDNS                            |
| **Local Listener** | `127.0.0.1:8321`                                                | Loopback bind on host                                                         |
| **Serve Bridge**   | `tailscale serve --bg http://localhost:8321`                    | Backend serves plain HTTP on 8321 (no TLS); Tailscale handles TLS termination |
| **OAuth Callback** | `https://<node-name>.<tailnet>.ts.net/api/auth/github/callback` | Configured in GitHub OAuth App                                                |
| **Push Protocol**  | RFC 8291 + RFC 8292                                             | Encrypted with subscriber P-256 key and auth token                            |
| **Service Worker** | `/sw.js`                                                        | Handles `push` and `notificationclick` events                                 |

---

## 3. Step-by-Step Operator Setup

### Step 3.1: Configure Tailscale Serve on the Host

1. Verify Tailscale is running on the host machine:

   ```bash
   tailscale status
   ```

2. Start Tailscale Serve for the dashboard backend port (default 8321). The
   backend serves plain HTTP on 8321 (no TLS); Tailscale terminates TLS at
   the tailnet edge:

   ```bash
   tailscale serve --bg http://localhost:8321
   ```

3. **Verify Tailscale Funnel is OFF** (mandatory per ADR-0007):
   ```bash
   tailscale funnel status
   ```
   The output must confirm that no public funnel is enabled.

### Step 3.2: Generate VAPID Keypair

Generate a standard P-256 ECDSA keypair for VAPID signing:

```bash
python -m push keygen
```

The tool outputs the three required environment variables formatted for pasting:

```dotenv
VAPID_PUBLIC_KEY=<65-byte uncompressed P-256 point, base64url-encoded>
VAPID_PRIVATE_KEY=<32-byte scalar, base64url-encoded>
VAPID_SUBJECT=mailto:operator@example.com
```

### Step 3.3: Configure Host Environment & Restart Service

1. Open your host's environment file (e.g. `~/.config/runner-dashboard/env` or systemd service environment file):

   ```dotenv
   DASHBOARD_TLS=1
   DASHBOARD_PUBLIC_ORIGIN=https://<node-name>.<tailnet>.ts.net
   VAPID_PUBLIC_KEY=<your-vapid-public-key>
   VAPID_PRIVATE_KEY=<your-vapid-private-key>
   VAPID_SUBJECT=mailto:operator@example.com
   ```

2. Restart the dashboard service:

   ```bash
   sudo systemctl restart runner-dashboard
   ```

3. Verify startup in the logs:
   ```bash
   journalctl -u runner-dashboard -n 50 --no-pager | grep -i "web push"
   ```
   You should see:
   ```
   Web Push configured (subject=mailto:operator@example.com)
   ```
   _(Note: Key material is never logged)._

### Step 3.3a: Sign In via Tailscale Identity (issue #1755)

With `tailscale serve` bridging the phone's tailnet connection to the local
dashboard, the resolved client address becomes the phone's tailnet address —
so the loopback-admin bypass no longer applies, and a phone with no GitHub
OAuth app configured has no way to sign in. Owner decision 2026-09-28: phone
sign-in uses Tailscale's own identity headers instead, making GitHub OAuth
optional for tailnet-only deployments.

1. Set the two environment variables in the same host environment file used
   in Step 3.3:

   ```dotenv
   DASHBOARD_TAILSCALE_AUTH=1
   DASHBOARD_TAILSCALE_LOGINS=<your tailscale login>
   ```

   `DASHBOARD_TAILSCALE_LOGINS` accepts a comma-separated allow-list of
   Tailscale logins (the value Tailscale sends in the `Tailscale-User-Login`
   header, matched case-insensitively). Only logins on this list are
   admitted.

2. Restart the dashboard service so the new environment takes effect.

3. A request is only admitted when it is genuinely relayed by the local
   `tailscaled` process: the raw transport peer (recorded before any
   proxy-header rewriting) must be loopback, and the resolved client address
   must fall in a Tailscale range. A caller who reaches the dashboard
   directly over the tailnet — bypassing `tailscale serve` — cannot forge the
   `Tailscale-User-Login` header to gain access, because their raw transport
   peer is never loopback.

4. The admitted principal has exactly the same power as the local loopback
   development admin (`roles: ["loopback"]`) — no more, no less.

### Step 3.4: Install PWA on Phone (iOS / Android)

1. Connect your phone to Tailscale and ensure VPN status is connected.
2. Open Safari on iOS (or Chrome on Android) and navigate to `https://<node-name>.<tailnet>.ts.net`.
3. In Safari:
   - Tap the **Share** button in the navigation toolbar.
   - Scroll down and tap **Add to Home Screen**.
   - Confirm the app title ("Runner Dashboard") and tap **Add**.
4. Close Safari and tap the new **Runner Dashboard** icon on your Home Screen.
5. Log in with your authorized GitHub credentials.

### Step 3.5: Enable Push Notifications & Test

1. In the Runner Dashboard PWA, navigate to **Settings -> Push Notifications** (`/settings/push`).
2. Verify the configuration status banner reads:
   _"Web Push transport is active with VAPID authentication on this tailnet."_
3. Toggle your desired alert topics:
   - `staff.escalation` (approvals, questions from Barb and staff roles)
   - `build.failure` (CI / workflow failures)
   - `conductor.stalled_run` (stalled agent dispatches)
4. Tap **Subscribe**. When prompted by iOS / browser, tap **Allow Notifications**.
5. Once subscribed, tap **Send test notification**.
6. A native notification will appear on your phone:
   - **Title**: `Runner Dashboard`
   - **Body**: `Web Push is operational on this device.`
7. Tap the notification to confirm that it focuses the dashboard PWA.

---

## 4. Mobile Staff Console Experience

The mobile console is optimized for phone thumb reach (WCAG 2.5.5):

- **Bottom Tab Navigation**: Switch smoothly between **Console** (Roster and Barb concierge), **Inbox** (Attention items waiting on you), and **Runs** (Real-time run scorecard and history).
- **Thumb Actions on Proposals**: When Barb or a specialist requests an action approval, large thumb-friendly **Approve** (green) and **Deny** (red) action targets (≥ 44px) appear directly in the conversation thread.
- **Safe-Area Pinning**: The message composer is permanently pinned above the iOS Home bar using `env(safe-area-inset-bottom)` without obscuring conversation history.

---

## 5. Troubleshooting & FAQ

### 1. WSL2 Mirrored Port Conflict (`[Errno 98] Address already in use`)

If WSL2 cold-starts with mirrored networking (`.wslconfig: networkingMode=mirrored`), Windows-side `tailscaled.exe` may contend for port 8321.

- Follow [`docs/wsl-mirrored-port-conflict.md`](docs/wsl-mirrored-port-conflict.md) to install `deploy/wsl-mirrored-port-helper.sh` in `ExecStartPre` / `ExecStartPost`.

### 2. VAPID 503 Service Unavailable (`Web Push is not configured on this server`)

- Verify that `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and `VAPID_SUBJECT` are defined in the service environment.
- Verify that the subject starts with either `mailto:` or `https:`.

### 3. iOS Push Notifications Don't Arrive

- Ensure the app was launched from the **Home Screen icon** and not directly in a Safari tab.
- Check iOS **Settings -> Runner Dashboard -> Notifications** to ensure permissions are granted.
- Ensure your phone has active connection to your Tailscale network.

### 4. Expired or Purged Subscriptions (404 / 410)

- The backend automatically purges subscriptions when the push service reports them as expired (`410 Gone` or `404 Not Found`).
- To re-register, simply visit `/settings/push` in the PWA and tap **Subscribe**.
