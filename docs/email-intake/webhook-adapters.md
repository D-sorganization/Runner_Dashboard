# Email intake — webhook adapters (future slices)

The default path is **IMAP pull** (no public HTTP endpoint on the hub). The
`InboundAdapter` interface in `backend/email_intake/adapters/base.py` is the
contract for later provider-specific adapters:

| Adapter | Source | Notes |
| --- | --- | --- |
| `imap` | Poll mailbox | Shipped in epic #2092 |
| `cloudflare-email-routing` | HTTP webhook | Normalize to `InboundMessage` |
| `postmark` | Inbound webhook JSON | Map `Authentication-Results` from headers |
| `mailgun` | Routes API webhook | |
| `ses` | SNS → Lambda → hub | |

Each adapter must populate:

- `message_id`, `from_address`, `from_domain`, `received_at`
- `authentication_results` from the **receiving host** (aligned DMARC check)
- `body_plain` (never HTML execution), attachment metadata only

Webhook adapters are **not** registered until a follow-up slice adds routing and
signature verification for that provider.
