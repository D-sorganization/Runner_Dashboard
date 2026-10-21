# Email intake runbook

Provider-neutral work requests via IMAP pull (epic #2092). No public webhook on the hub.

## 1. Mailbox and DNS

Create a dedicated mailbox (example: `intake@<subdomain>.affinedrift.com`) at any host
that supports IMAP. Replace `<subdomain>` with the label Dieter chooses (not chosen yet).
Suggested DNS for `<subdomain>.affinedrift.com`:

| Type | Name | Value |
| --- | --- | --- |
| MX | `<subdomain>.affinedrift.com` | your provider MX targets |
| TXT | `<subdomain>.affinedrift.com` | `v=spf1 include:<provider-spf> -all` |
| TXT | `default._domainkey.<subdomain>.affinedrift.com` | provider DKIM selector |
| TXT | `_dmarc.<subdomain>.affinedrift.com` | `v=DMARC1; p=quarantine; rua=mailto:dmarc@affinedrift.com` |

DMARC alignment must pass on the **receiving host**; the hub checks
`Authentication-Results` for `dmarc=pass` aligned with the `From` domain.

## 2. Repository_Management policy

`staff/mail_policy.yml` is added in Repository_Management (companion tracked in
`docs/email-intake/companion/` until the RM PR merges — link the RM PR here once open).
RM sync refreshes the hub cache on each `staff.rm_sync` tick.

Kill switch: set `intake_enabled: false` in that file and wait for sync (next sync
overwrites the cached policy; restart not required for policy-only changes).

## 3. Hub environment (DeskComputer)

Add to the hub service environment file (never commit secrets), e.g.
`/etc/runner-dashboard/env` or the path configured for `runner-dashboard.service`:

```bash
MAIL_INTAKE_ENABLED=1
MAIL_INTAKE_ADDRESS=intake@<subdomain>.affinedrift.com
MAIL_INTAKE_DOMAIN=<subdomain>.affinedrift.com
MAIL_INTAKE_IMAP_HOST=imap.<provider>
MAIL_INTAKE_IMAP_USER=intake@<subdomain>.affinedrift.com
MAIL_INTAKE_IMAP_PASSWORD_FILE=/etc/runner-dashboard/mail-intake-imap.password
MAIL_INTAKE_POLL_INTERVAL_SECONDS=120
```

Every accepted email must include a `role:` line naming a **published staff role**
(from `staff/roles/*.yml` under `STAFF_RM_ROOT`). There is no default role; unknown
roles are quarantined.

Restart the **system** dashboard unit on the hub after env or credential changes:

```bash
sudo systemctl restart runner-dashboard
```

On spoke nodes (e.g. Brick) that run the dashboard as a **user** unit, use
`systemctl --user restart runner-dashboard.service` instead.

## 4. Swapping mail providers

1. Update MX to the new provider.
2. Update `MAIL_INTAKE_IMAP_*` credentials to the new mailbox.
3. Restart the dashboard service (system unit on hub, user unit on spokes).

## 5. Barb (agent-grok) — read and approve proposals

```bash
export FLEET_API_TOKEN="$(cat ~/.config/runner-dashboard/agent-tokens/agent-grok.token)"
BASE=http://127.0.0.1:8321

curl -sS -H "Authorization: Bearer $FLEET_API_TOKEN" \
  "$BASE/api/v1/proposals?state=pending" | jq .

curl -sS -H "Authorization: Bearer $FLEET_API_TOKEN" \
  "$BASE/api/v1/proposals/ip_XXXXXXXXXXXX" | jq .

# After Dieter approves in chat, Barb records that reference and approves:
curl -sS -X POST -H "Authorization: Bearer $FLEET_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"decision":"approved","reason":"Dieter approved dispatch","owner_approval_ref":"Dieter approved in Signal 2026-10-21","execute_dispatch":true}' \
  "$BASE/api/v1/proposals/ip_XXXXXXXXXXXX/decide" | jq .
```

`owner_approval_ref` is **required** when the caller is `agent-grok`. Operators with
`staff.approve` may approve without it.

## 6. Health

`GET /api/fleet/status` includes `_email_intake` with `quarantine_count` and config flags.
