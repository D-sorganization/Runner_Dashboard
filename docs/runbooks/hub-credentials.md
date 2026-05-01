# Hub Fleet Token — Rotation Runbook

## Overview

`HUB_FLEET_TOKEN` is the intra-fleet bearer token used by spoke nodes when
proxying requests to the hub node.  It replaces forwarding the caller's own
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

The hub must validate inbound `Authorization: Bearer <HUB_FLEET_TOKEN>` headers
on routes that accept spoke traffic.

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
