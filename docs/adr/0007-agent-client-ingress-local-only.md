# 0007. Agent client ingress stays local-only

## Status

Accepted. Owner decision (Dieter Olson, 2026-09-27), recorded on
Repository_Management#1335 (SC-F6, part of SC-F #1352 under the Staff
Console epic #1354).

## Context

Cloud-hosted agent clients — Grok's servers, claude.ai connectors — cannot
reach the dashboard, because it listens only on the tailnet. RM#1676 planned
a Tailscale Funnel plus a custom MCP connector to close that gap, and
`docs/tailscale-funnel.md` already documents exposing one endpoint (the
Linear webhook receiver) through Funnel. CORS on the API is localhost-only
(`backend/server.py`, `_AUTH_EXEMPT_PATHS` area). Exposing any part of the
dashboard publicly is an owner decision, not something an agent can enable
unilaterally.

Three options were compared:

- **(a) Path-limited Funnel** — a Tailscale Funnel restricted to a dedicated
  reverse proxy that exposes only `/api/v1/staff` conversation routes, so the
  rest of the dashboard stays off the public internet.
- **(b) Outbound relay** — the dashboard polls an external queue for agent
  messages instead of accepting inbound connections, so no port is opened to
  the internet.
- **(c) Stay local-only** — agent clients reach the dashboard only from
  machines already on the tailnet or fleet, through the existing local
  tooling. No new public surface at all.

### Threat model

| Threat                        | Exposure under (a) / (b)                                                                                                             | Exposure under (c)                                                                                    |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| Token theft                   | A leaked staff token would authenticate from anywhere on the public internet.                                                        | A leaked token still only works from a network position that already has broader access to the fleet. |
| Prompt injection via messages | An external sender can post messages into a staff thread; a compromised or malicious cloud client becomes a remote injection vector. | The message path stays behind the tailnet; injection is possible only from an already-trusted client. |
| Replay                        | A public endpoint needs its own replay defenses independent of network trust.                                                        | Replay is bounded by the same network trust as the rest of the fleet.                                 |
| DoS                           | Publicly reachable routes, even path-limited ones, are a DoS target from the open internet.                                          | Not publicly reachable, so this class of DoS is not introduced.                                       |
| Cost exhaustion               | An external caller could trigger repeated dispatches or provider calls against the owner's accounts.                                 | Bounded by who already has fleet network access.                                                      |

No option is risk-free, but (a) and (b) both add a new externally reachable
surface, and every control listed under Consequences below would need to be
built and verified before either could be considered safe to enable.

## Decision

Stay local-only: option (c). No Funnel and no outbound relay are enabled by
this issue or this ADR. Agent clients reach the dashboard only from fleet
machines that already have tailnet access.

## Consequences

- Cloud-hosted agent clients (Grok's servers, claude.ai connectors) cannot
  talk to the dashboard directly. Grok Bot already works around this with its
  own local tool, which runs on a fleet machine and reaches the dashboard
  over the tailnet like everything else.
- No new public attack surface is added. CORS stays localhost-only and
  `docs/tailscale-funnel.md`'s existing Funnel use (the Linear webhook) is
  unaffected.
- RM#1676's Funnel-plus-connector plan does not proceed for staff conversation
  routes.
- If a future need reopens this question, any exposed option must first
  implement all of the following controls, not a subset:
  - **Per-agent minimal-scope tokens** — each agent client gets its own
    token scoped to only the routes it needs, never a shared or
    dashboard-wide credential.
  - **Rate limits** (SC-F7) — enforced at the exposed edge, not only inside
    the application.
  - **Audit logging** (SC-A8) — every request through the exposed surface is
    attributable and reviewable after the fact.
  - **Identity allowlist** — the exposed endpoint accepts requests only from
    a known, explicitly approved set of callers.
  - **Kill switch** — the owner can disable the exposed surface immediately,
    without a deploy, if it is abused.

## Revisit trigger

Revisit this decision if a cloud-only client becomes necessary — that is, a
client that cannot be given tailnet or fleet-machine access and must reach
the dashboard from an arbitrary public network location.
