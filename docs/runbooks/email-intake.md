# Email intake runbook

Provider-neutral work requests via IMAP pull (epic #2092). No public webhook on the hub.

## 1. Mailbox and DNS (subdomain of affinedrift.com)

Create a dedicated mailbox (example: `intake@mail.affinedrift.com`) at any host that
supports IMAP. Suggested DNS for `mail.affinedrift.com`:

| Type | Name | Value |
| --- | --- | --- |
| MX | `mail.affinedrift.com` | your provider MX targets |
| TXT | `mail.affinedrift.com` | `v=spf1 include:<provider-spf> -all` |
| TXT | `default._domainkey.mail.affinedrift.com` | provider DKIM selector |
| TXT | `_dmarc.mail.affinedrift.com` | `v=DMARC1; p=quarantine; rua=mailto:dmarc@affinedrift.com` |

DMARC alignment must pass on the **receiving host**; the hub checks
`Authentication-Results` for `dmarc=pass` aligned with the `From` domain.

## 2. Repository_Management policy

Ensure `staff/mail_policy.yml` exists (see `docs/email-intake/companion/`). RM sync
refreshes the hub cache on each `staff.rm_sync` tick.

Kill switch: set `intake_enabled: false` in that file and wait for sync (or restart
after editing cached policy is unnecessary — next sync overwrites).

## 3. Hub environment

Add to `~/.config/runner-dashboard/env` (never commit secrets):

```bash
MAIL_INTAKE_ENABLED=1
MAIL_INTAKE_ADDRESS=intake@mail.affinedrift.com
MAIL_INTAKE_DOMAIN=mail.affinedrift.com
MAIL_INTAKE_IMAP_HOST=imap.<provider>
MAIL_INTAKE_IMAP_USER=intake@mail.affinedrift.com
MAIL_INTAKE_IMAP_PASSWORD_FILE=~/.config/runner-dashboard/mail-intake-imap.password
MAIL_INTAKE_POLL_INTERVAL_SECONDS=120
MAIL_INTAKE_DEFAULT_DISPATCH_ROLE=barb
```

Restart the dashboard service after changing env:

```bash
systemctl --user restart runner-dashboard.service
```

## 4. Swapping mail providers

1. Update MX to the new provider.
2. Update `MAIL_INTAKE_IMAP_*` credentials to the new mailbox.
3. Restart `runner-dashboard.service`.

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
