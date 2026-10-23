# Shared Memory in Dashboard Barb Chat

The dashboard can add a read-only shared-memory context pack to Barb's prompt. This server-side connection is
separate from the Fleet MCP/CLI client's `FLEET_API_URL` and `FLEET_API_TOKEN`. Configure the dashboard service
with the URL and token-file settings below to opt in:

| Variable                        | Contract                                                                                                                                                                                  |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `BARB_SHARED_MEMORY_URL`        | Explicit opt-in shared API origin, such as `https://controltower.example`; only the fixed `/api/v1/memory/context-pack` path is requested.                                                |
| `BARB_SHARED_MEMORY_TOKEN_FILE` | Absolute token file: `memory.read` for context only; explicitly opted-in summaries also require `memory.shared.summarize`. Never grant general `memory.write`. On POSIX, use mode `0600`. |
| `BARB_SHARED_MEMORY_ALLOW_HTTP` | Optional operator opt-in for non-loopback HTTP; set to `1` only when the origin is protected by a trusted private transport.                                                              |

The URL must be a bare HTTP(S) origin without embedded credentials, a path, query or fragment, and must fit
within 256 characters. HTTPS is the default. Plain HTTP is allowed for loopback origins; remote HTTP requires
`BARB_SHARED_MEMORY_ALLOW_HTTP=1`. Use that exception only on an operator-trusted private transport such as a
verified tailnet, never on a public network. The client does not use environment proxies and does not follow
redirects. The dashboard uses its dedicated token only after the original Barb-chat caller is independently
authorized for `memory.read`. No token is inserted into the prompt or included in log messages.

When `BARB_SHARED_MEMORY_URL` is unset, the integration is off. Once the URL is configured, the token-file
setting is required; a missing or unreadable token produces an unavailable block rather than disabling the
integration silently. When enabled, the HTTP request has a two-second deadline. The bounded token-file read
happens before that asynchronous HTTP deadline. The client requests identity encoding and rejects compressed
responses, reads at most 4 KiB of token data and accepts at most 64 KiB of response data. It validates the API
response against the `MemoryContextPack` model and adds at
most 8,000 characters to the prompt. The prompt labels the content as untrusted reference material and identifies
its source and fetch time. If the caller lacks `memory.read` or the configured source cannot be read, Barb receives
a generic unavailable block; the request is not forwarded and the dashboard does not fall back to its local memory
database.

This setting does not set or change `BARB_AUTHORITY_NODE`, `BARB_AUTHORITY_URL` or `BARB_AUTHORITY_EPOCH`. Barb's
conversation authority remains governed by [ADR 0008](../adr/0008-barb-single-authority.md).

Automatic summaries are off by default. Enabling `BARB_SHARED_MEMORY_AUTO_SUMMARIES=1` explicitly opts this
connection into bounded digest writes at the same canonical origin. Provision the dedicated bridge principal
with exactly `memory.read` and `memory.shared.summarize`, with no roles or general `memory.write` permission.
Existing read-only installations remain read-only until an operator performs this separate scope change.
The original Barb-chat caller must independently have `memory.shared.summarize`; granting it to the bridge
does not authorize every operator's conversation. A read-only bridge token receives HTTP 403 on a digest
POST, and the completed reply reports saving as unavailable rather than silently using a different token.

The provider appends one bounded `staff-memory` capsule after its reply. Recognized top-level capsules,
including incomplete or ambiguous ones, never reappear in the persisted reply after the streaming filter
hides them. Only one complete final valid capsule is eligible for saving. The application reports the
acknowledged save/replay outcome; rejected or unconfirmed writes retain the reply and have no local-memory
fallback or transcript outbox. This feature never changes directives, holds or Barb authority.
