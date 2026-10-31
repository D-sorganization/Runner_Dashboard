# Assistant Bridge Contract

Runner Dashboard is the **shared context hub** for personal-assistant agents (Barb on Grok, Codex hands, future Claude or other products). Each assistant connects with its own **principal** and bearer token; behavior is provider-agnostic.

## Roles

| Role              | Principal (today)                | Job                                                                            |
| ----------------- | -------------------------------- | ------------------------------------------------------------------------------ |
| Front door        | `agent-grok` (Barb)              | Intake, routing, owner-facing proposals; notifies Dieter when needed           |
| Hands             | `agent-codex-hands`              | Execute work (computer control, dispatch); reports back via memory and threads |
| VM workers        | `agent-grok-vm`, `agent-duet`    | barb-worker VM identities (read + shared memory only); no dispatch or holds    |
| CLI orchestrators | `agent-codex`, `agent-claude`, … | Existing MCP/CLI paths; see per-product guides                                 |

Barb is the **single front door** for Dieter-facing notifications. Other assistants are **hands**: they take assigned work, follow holds and directives from the dashboard, and **do not** ping Dieter directly—post updates to shared memory or staff threads instead.

## Session Lifecycle

1. **Start:** `GET /api/v1/memory/context-pack` with the assistant bearer (see [grok.md](grok.md) and [codex-hands.md](codex-hands.md) for curl recipes).
2. **Work:** Respect active **holds** and **directives** in the pack; use staff APIs within the principal's scopes.
3. **Checkpoints:** bounded Codex and Dashboard turn summaries use `POST /api/v1/memory/session-digest` with the dedicated `memory.shared.summarize` scope; the route accepts only a short summary, up to three decisions and three open items, and deduplicates the authenticated writer + product + conversation + user-message tuple. Do not send raw transcripts to this route.
4. **End:** Barb appends a final `session_digest` summarizing the conversation, decisions, and follow-ups.
5. **Tasks:** Mirror the owner's task list with `entry_type: task` (append-only; supersede prior rows with `supersedes_id` when status changes).

Never log or print bearer tokens. Tokens live only in `~/.config/runner-dashboard/agent-tokens/<principal-id>.token` on the machine that calls the hub.

## Configuration

Repo defaults: `config/agent_principals.yml` (`assistant_profile` + per-agent `kind` and scopes). Tokens are ingested from `agent-tokens/*.token` on dashboard restart. See comments in that file for minting without echoing the secret.

## Guides

- [Grok / Barb](grok.md)
- [Codex hands](codex-hands.md)
- [Codex CLI (MCP)](codex.md)
- [Claude](claude.md)
- [Barb exchange repo](barb-exchange.md)
- [Role seats for Barb's Grok Bot roles (proposed)](role-seats.md)
- [Connect index](connect.md)
