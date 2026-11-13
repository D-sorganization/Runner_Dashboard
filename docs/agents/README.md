# Assistant Bridge Contract

Runner Dashboard is the **shared context hub** for personal-assistant agents (Barb on Grok, Codex hands, future Claude or other products). Each assistant connects with its own **principal** and bearer token; behavior is provider-agnostic.

## Roles

| Role              | Principal (today)                                  | Job                                                                            |
| ----------------- | -------------------------------------------------- | ------------------------------------------------------------------------------ |
| Front door        | `agent-grok` (Barb)                                | Intake, routing, owner-facing proposals; notifies Dieter when needed           |
| Hands             | `agent-codex-hands`                                | Execute work (computer control, dispatch); reports back via threads and turnover |
| CLI orchestrators | `agent-codex`, `agent-claude`, `agent-grok-cli`, … | Existing MCP/CLI paths; see per-product guides                                 |

Barb is the **front door** for Dieter-facing notifications (using principal `agent-grok`, distinct from the standalone `grok_cli` coding CLI in [grok-cli.md](grok-cli.md)), and any agent (Grok Barb, rd-barb, the proposed Claude planner in [claude-planner.md](claude-planner.md), or a hand) may contact Dieter directly as long as Grok Barb is copied (Dieter, 2026-10-27), through a staff thread or a `to: barb` envelope, so the front door keeps the full picture. Hands still take assigned work, follow holds and directives from the dashboard, and post routine updates to staff threads and Barb turnover.

## Session Lifecycle

1. **Start:** read Barb continuity (`GET /api/v1/staff/barb/continuity`), priorities (`GET /api/priorities`), and holds (`GET /api/v1/staff/holds`). See [grok.md](grok.md) and [codex-hands.md](codex-hands.md) for curl recipes. The retired cross-agent layer (`GET /api/v1/memory/context-pack` and shared digests) is not part of session startup (owner decision 2026-11-12).
2. **Pick work:** List claimable issues with `GET /api/coordination/jobs`, claim with `POST /api/coordination/claims`, and release with `POST /api/coordination/claims/release` when finished (Fleet Command → Claims → Job board, or `GET /api/coordination/briefing` for `jobs_hint`).
3. **Work:** Respect active **holds** and **directives** from priorities; use staff APIs within the principal's scopes. When holding a Barb lease, send `X-Barb-Scope` and `X-Barb-Lease-Epoch` on fenced writes (see [operating-the-fleet-through-the-dashboard.md](../operating-the-fleet-through-the-dashboard.md)).
4. **Turnover:** while holding a Barb scope, log shift turnover with `POST /api/v1/staff/barb/turnover` before `POST .../leases/release` (or supply `turnover_summary` on release). See [tooling-capabilities-matrix.md](tooling-capabilities-matrix.md).
5. **Inventory:** optional task/desktop handoff state via `GET/POST /api/v1/staff/barb/inventory` when coordinating multi-surface work.

Never log or print bearer tokens. Tokens live only in `~/.config/runner-dashboard/agent-tokens/<principal-id>.token` on the machine that calls the hub.

## Configuration

Repo defaults: `config/agent_principals.yml` (`assistant_profile` + per-agent `kind` and scopes). Tokens are ingested from `agent-tokens/*.token` on dashboard restart. See comments in that file for minting without echoing the secret.

## Guides

- [Grok / Barb](grok.md)
- [Grok CLI (coding provider)](grok-cli.md)
- [Codex hands](codex-hands.md)
- [Codex CLI (MCP)](codex.md)
- [Claude](claude.md)
- [Claude planner seat and Barb channel (proposed)](claude-planner.md)
- [Persistent messenger agents (proposed)](messenger-agents.md)
- [Connect index](connect.md)
