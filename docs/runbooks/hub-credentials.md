# Hub Fleet Token — Rotation Runbook

## Overview

`HUB_FLEET_TOKEN` is the intra-fleet bearer token used by spoke nodes when
proxying requests to the hub node. It replaces forwarding the caller's own
`Authorization` / `Cookie` / `X-API-Key` headers (issue #347).

## Setting the token

On both the **hub** and each **spoke** node:

```bash
# Generate a secure random token (32 bytes → 44 base64 chars)
HUB_FLEET_TOKEN=$(python3 -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode())")

# Add/update in the dashboard env file
echo "HUB_FLEET_TOKEN=${HUB_FLEET_TOKEN}" >> ~/.config/runner-dashboard/runner-dashboard.env

# Restart the dashboard service
sudo systemctl restart runner-dashboard
```

## Enforcement (issue #922)

The hub validates inbound `Authorization: Bearer <HUB_FLEET_TOKEN>` on
hub-reachable fleet-read routes via the `require_fleet_peer` dependency
(`backend/identity.py`). A request to a gated route is accepted when **either**:

- it carries valid operator credentials (a principal resolved from a service
  token or session), **or**
- it presents `Authorization: Bearer <HUB_FLEET_TOKEN>` matching the hub's
  configured token, compared with `hmac.compare_digest` (constant-time).

**Policy — fleet reads are token-gated only when a token is configured.**
When `HUB_FLEET_TOKEN` is **set** on the hub, an unauthenticated caller to a
gated fleet route receives `401`. When it is **unset** (single-node and
token-less deployments), fleet reads are tailnet-public and the dependency is a
no-op — preserving backward compatibility. To enforce the fleet trust boundary,
set `HUB_FLEET_TOKEN` on the hub (and on every spoke, so their proxied calls
carry it).

> Currently gated: `GET /api/fleet/status`. Additional hub-reachable read routes
> are being brought under the same dependency; see issue #924 for the structural
> "all `/api/*` routes authenticated" follow-up.

## Rotation procedure

1. Generate a new token on the hub (see above).
2. Set `HUB_FLEET_TOKEN` on the hub **first** and restart it.
3. Update each spoke node one at a time and restart.
4. Verify connectivity with `GET /api/fleet/status` from each spoke.

**No downtime is required** — the old token continues to work on spokes
until each node is restarted with the new token, and hub nodes can be
configured to accept both tokens during the transition window if needed.

## Security notes

- Rotate at least every 90 days, or immediately if the token is suspected compromised.
- The token is a symmetric shared secret; protect it like an API key.
- Never commit `HUB_FLEET_TOKEN` to the repository; store it in the env file
  (which is excluded by `.gitignore`).
- If not set, no `Authorization` header is injected for intra-fleet calls
  (the hub must allow unauthenticated spoke traffic in that case).
