# Tailscale Funnel for Linear Webhooks

**Issue:** #242 — Linear webhook receiver security

The Linear webhook endpoint (`POST /api/linear/webhook`) is designed to receive events from Linear's servers. Because Linear cannot reach a machine behind NAT, we expose the webhook through **Tailscale Funnel**.

## Architecture

```
┌─────────────┐         ┌──────────────────┐         ┌────────────────┐
│  Linear.app │────────▶│ Tailscale Funnel │────────▶│ runner-dashboard│
│  (origin)   │  HTTPS  │  (public ingress)│  HTTP   │  :8321/webhook  │
└─────────────┘         └──────────────────┘         └────────────────┘
```

- Tailscale Funnel terminates TLS and forwards HTTP to the local dashboard.
- The dashboard validates the `Linear-Signature` header against a shared secret.
- CSRF checks are bypassed for `/api/linear/webhook` (it is listed in `_AUTH_EXEMPT_PATHS`).

## Setup

### 1. Enable Funnel on the Tailscale node

```bash
# On the machine running runner-dashboard
sudo tailscale funnel 8321
```

Or with a systemd service override:

```ini
# /etc/systemd/system/runner-dashboard.service.d/funnel.conf
[Service]
Environment="TAILSCALE_FUNNEL=1"
```

### 2. Configure Linear

1. Open **Linear → Settings → API → Webhooks**.
2. Create a new webhook with the URL:
   ```
   https://<tailscale-node-name>.ts.net/api/linear/webhook
   ```
3. Set the **Secret** field — this becomes the shared `LINEAR_WEBHOOK_SECRET`.
4. Select events: `Issue`, `Comment`, `Cycle`, `Project`.

### 3. Set the environment variable

Add to `~/.config/runner-dashboard/env`:

```bash
LINEAR_WEBHOOK_SECRET="whsec_..."  # pragma: allowlist secret
```

Restart the dashboard:

```bash
sudo systemctl restart runner-dashboard
```

### 4. Verify

Check the health endpoint:

```bash
curl https://<tailscale-node-name>.ts.net/api/linear/webhook/health
```

Expected response:

```json
{
  "status": "ok",
  "signature_verification": "enabled",
  "max_age_seconds": 300,
  "replay_buffer_size": 0
}
```

## Security Considerations

| Control | Purpose |
|---------|---------|
| Tailscale Funnel | TLS termination + IP allow-list via Tailnet ACLs |
| `Linear-Signature` HMAC-SHA256 | Payload authenticity |
| Max-age check (300 s) | Replay protection |
| Webhook ID deduplication | Idempotency |
| No CSRF on webhook route | External service compatibility |
| Sanitized logging | Prevents secret leakage |

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `401 Signature verification failed` | Secret mismatch | Verify `LINEAR_WEBHOOK_SECRET` matches Linear console |
| `400 Payload too old` | Clock skew | Sync NTP on both ends |
| `200 replay: true` | Duplicate webhook ID | Normal — Linear retries; no action needed |
| Funnel not reachable | Firewall / ACL | Ensure `tailscale status` shows ` funnel` next to the machine |

## Production Hardening

- Replace in-memory `_processed_webhook_ids` with Redis or a persistent store.
- Set a short TTL on webhook ID records (e.g. 24 hours).
- Monitor `/api/linear/webhook/health` with your alerting stack.
- Restrict Tailscale ACL tags to the dashboard node.

## References

- [Tailscale Funnel docs](https://tailscale.com/kb/1223/funnel)
- [Linear Webhooks docs](https://developers.linear.app/docs/graphql/webhooks)
- SPEC.md § "Linear Webhook Integration"
