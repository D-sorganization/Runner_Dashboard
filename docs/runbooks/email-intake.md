# Email intake runbook

Provider-neutral work requests via IMAP pull (epic #2092). No public webhook on the hub.

**Default:** intake is **off** until `MAIL_INTAKE_ENABLED=1` on the hub. Do not commit IMAP credentials;
use a password file on the host only.

## 1. Mailbox and DNS

Production mailbox: **`dashboard@example.com`** (root domain; provision this address on your provider).

The hub receives mail via your provider’s IMAP settings for that mailbox. For **outbound senders**
(allowlisted in RM `staff/mail_policy.yml`), DMARC alignment uses the **From** domain — typically
<<<<<<< HEAD
`@example.com`, `@work.example.com`, etc. Ensure **example.com** publishes correct SPF, DKIM, and
DMARC if anyone sends From `@work.example.com`.
=======
`@example.com`, `@affinedrift.com`, etc. Ensure **affinedrift.com** publishes correct SPF, DKIM, and
DMARC if anyone sends From `@affinedrift.com`.
>>>>>>> origin/main

| Type | Name | Purpose |
| --- | --- | --- |
| MX | `example.com` | Provider MX for the mailbox host (if receiving at root) |
| TXT | `example.com` | SPF for example.com senders |
| TXT | provider DKIM selector | DKIM for work.example.com |
| TXT | `_dmarc.example.com` | DMARC policy for example.com |

DMARC must pass on the **receiving host**; the hub checks `Authentication-Results` for
`dmarc=pass` aligned with the `From` domain.

## 2. Repository_Management policy

Canonical policy: [Repository_Management#2284](https://github.com/D-sorganization/Repository_Management/pull/2284)
(`staff/mail_policy.yml` + validator, merged `e524c89`).

A copy for offline reference lives under `docs/email-intake/companion/`. RM sync refreshes the hub
cache on each `staff.rm_sync` tick.

Kill switch: set `intake_enabled: false` in `staff/mail_policy.yml` and wait for sync (restart not
required for policy-only changes).

## 3. Hub environment (hub-a)

Add to the hub **system** service environment file (never commit secrets), e.g.
`/etc/runner-dashboard/env`:

```bash
# Off by default — set to 1 only when ready to poll
# MAIL_INTAKE_ENABLED=1
# MAIL_INTAKE_ADDRESS=dashboard@example.com
# MAIL_INTAKE_DOMAIN=example.com
# MAIL_INTAKE_IMAP_HOST=imap.<provider>
# MAIL_INTAKE_IMAP_USER=dashboard@example.com
# MAIL_INTAKE_IMAP_PASSWORD_FILE=/etc/runner-dashboard/mail-intake-imap.password
# MAIL_INTAKE_POLL_INTERVAL_SECONDS=120
```

Every accepted email must include a `role:` line naming a **published staff role**
(`staff/roles/*.yml` under `STAFF_RM_ROOT`). There is no default role; unknown roles are quarantined.

After enabling intake or changing credentials:

```bash
sudo systemctl restart runner-dashboard
```

On spoke nodes (e.g. node-c) with a **user** unit: `systemctl --user restart runner-dashboard.service`.

## 4. Swapping mail providers

1. Update MX/DNS at the provider if needed.
2. Update `MAIL_INTAKE_IMAP_*` and the password file on the hub.
3. Restart the dashboard service (system unit on hub, user unit on spokes).

## 5. Barb (agent-grok) — read and approve proposals

```bash
export FLEET_API_TOKEN="$(cat ~/.config/runner-dashboard/agent-tokens/agent-grok.token)"
BASE=http://127.0.0.1:8321

curl -sS -H "Authorization: Bearer $FLEET_API_TOKEN" \
  "$BASE/api/v1/proposals?state=pending" | jq .

curl -sS -H "Authorization: Bearer $FLEET_API_TOKEN" \
  "$BASE/api/v1/proposals/ip_XXXXXXXXXXXX" | jq .

# After the owner approves in chat, Barb records that reference and approves:
curl -sS -X POST -H "Authorization: Bearer $FLEET_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"decision":"approved","reason":"Owner approved dispatch","owner_approval_ref":"Owner approved in chat 2026-10-21","execute_dispatch":true}' \
  "$BASE/api/v1/proposals/ip_XXXXXXXXXXXX/decide" | jq .
```

`owner_approval_ref` is **required** when the caller is `agent-grok`. Operators with
`staff.approve` may approve without it.

## 6. Health

`GET /api/fleet/status` includes `_email_intake` with `quarantine_count` and config flags.
