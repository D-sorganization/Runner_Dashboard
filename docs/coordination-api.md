# Fleet Coordination API

Issues #1229 and #1244 (hardening), epic #1192. One HTTP surface on every dashboard node through which any agent — Claude Code,
Codex, Gemini CLI, Grok Bot, staff runs, humans — sees who is working on what, exchanges messages, claims
issues and gets a pre-work briefing. Priorities (`/api/priorities`) are a sibling surface.

Code: `backend/routers/coordination.py` (routes), `backend/coordination/` (logic).

## Principles

- **No second message store.** Presence and messages stay on the Repository_Management board issue
  (#1576) through RM `scripts/agent_communicate.py`; leases stay as issue comments plus `claim:*` labels
  through RM `scripts/check_agent_claim.py`, `post_agent_lease.py` and `release_agent_lease.py`. The
  dashboard runs those scripts as subprocesses (`<STAFF_RM_PYTHON> -m scripts.<name>` with `cwd` =
  `STAFF_RM_ROOT`) and never imports RM code. The subprocess helper `coordination/rm_scripts.py` is shared
  with the staff lease ritual (`staff/lease.py`) and the usage export.
- **JSON everywhere, with `generated_at`.** Reads never fail because RM or GitHub does: they return HTTP 200
  with `{"available": false, "reason": "..."}`. Writes that the RM script rejects return **502** with
  `detail: {error, guidance}` taken from the script.
- **Cached board reads.** Board reads are cached in-process for 60 s. `list --all-repos` is used when RM
  supports it (one GitHub read serves every repository); otherwise the dashboard calls `list` once per fleet
  repository (`COORDINATION_REPOS`, comma separated, or every staff role's `repos:` plus
  Repository_Management) and merges the results; that fallback cannot see sessions in other repositories, so
  it is always `complete: false` with a warning (RM #1704 adds `--all-repos`). Every write clears the cache and
  bumps a generation counter, so a read that started before the write is never cached after it; concurrent
  cache misses share one board read.
- **Advisory presence, binding claims.** Presence is advisory. A claim held by anyone but this agent and
  session is a 409.

## Authentication

| Kind   | Dependency                    | Accepted callers                                                                                                                                      |
| ------ | ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Reads  | `require_fleet_peer`          | Any principal; the `HUB_FLEET_TOKEN` bearer when set; anyone on the tailnet when it is not set.                                                       |
| Writes | `require_coordination_writer` | A principal with scope `coordination.write` (presets `bot`, `operator`, `admin`), or the loopback orchestrator peer when `DASHBOARD_LOOPBACK_AUTH=1`. |

- The shared `HUB_FLEET_TOKEN` is **not** a write credential. Mint one bot service token per agent so every
  write is attributable.
- A principal without the scope gets 403; no credentials gets 401.
- Writes need the CSRF header `X-Requested-With: XMLHttpRequest`, bearer callers included.
- `agent` may be omitted from a write body when the caller is a bot principal: it defaults to the principal's
  agent (below). Otherwise a missing `agent` is a 422.
- `/api/coordination/` is in `_ALT_AUTH_EXEMPT_PREFIXES` (like `/api/staff/`) so fleet-token readers are not
  stopped by the structural perimeter; every route carries its own dependency.

### Impersonation rules

| Caller                                                             | May act as agent                         | May use session                        |
| ------------------------------------------------------------------ | ---------------------------------------- | -------------------------------------- |
| Bot principal `agent-<name>` (role `bot`)                          | only `<name>` (another `agent` is a 403) | only ids starting `<name>-` (else 403) |
| Bot principal not named `agent-<name>`                             | none: every coordination write is a 403  | none                                   |
| Human or bot with role `operator` / `admin`; loopback orchestrator | any agent in the RM roster               | any                                    |

**Session naming convention:** a session id is `<agent>-<unique suffix>`, e.g. `codex-20260923-1045` or
`claude-issue-1244`, and must match RM's identifier rule `^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`. Mint one bot
token per agent with principal id `agent-<agent>` (`agent-codex`, `agent-claude`, ...). The rule applies to
coordination writes only; other `coordination.write` users (e.g. `PUT /api/priorities/directives`) are
unaffected.

## Request contracts

| Field       | Rule                                                                                                          |
| ----------- | ------------------------------------------------------------------------------------------------------------- |
| `repo`      | repository name `^(?:owner/)?[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`; an `owner/` prefix is stripped, `..` rejected |
| `session`   | RM `_IDENTIFIER` `^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$` (also `to` and `message_id`)                            |
| `agent`     | `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` and in RM's roster (`shared_scripts.agent_identity.AGENT_IDS`)            |
| `issue`     | integer > 0                                                                                                   |
| `text`      | 1–4000 characters, not blank, no control characters except newline and tab                                    |
| `ttl_hours` | 0.1–8 (default 2)                                                                                             |
| `branch`    | `^[A-Za-z0-9][A-Za-z0-9._/@+-]{0,199}$`                                                                       |
| `paths`     | ≤ 50 entries, each ≤ 300 characters, not starting with `-`                                                    |
| `goals`     | ≤ 20 pairs; keys match RM `_IDENTIFIER`; outcomes 1–250 printable characters on one line                      |
| `to`        | a session id or `*` (everyone in `repo`)                                                                      |
| `intent`    | 1–200 printable characters on one line, not starting with `-` (default `implement`)                           |
| `reason`    | 1–300 printable characters on one line (claim release)                                                        |

Unknown body fields are rejected (422). "One line of printable text" excludes CR, LF, NEL, U+2028/2029 and every
other non-printable character: RM writes `intent` and `reason` into the lease comment, and a line break there
could forge an `<!-- agent-lease v1 -->` block. The roster is read once from the RM checkout
(`python -c "from shared_scripts.agent_identity import AGENT_IDS"`) and cached; when RM cannot answer, a static
copy (including `gemini` and `cursor-agent` from RM #1707) is used and the read is retried after 5 minutes.

## Endpoints

### `GET /api/coordination/sessions?repo=`

Board sessions (all repositories, optional case-insensitive `repo` filter) merged with this fleet's staff runs
in flight (the same fan-out as `/api/staff/summary`).

```json
{
  "available": true,
  "complete": true,
  "sessions": [
    {
      "session": "s-42",
      "agent": "codex",
      "repo": "Tools",
      "issue": 42,
      "branch": "feat/x",
      "paths": ["a.py"],
      "goals": { "api": "ship" },
      "expires": "…",
      "at": "…",
      "source": "board"
    }
  ],
  "staff_runs": [
    {
      "id": "run-1",
      "role": "night-watch",
      "provider": "claude",
      "machine": "Desk",
      "repo": "Tools",
      "target": "#5",
      "status": "running",
      "started_at": "…",
      "source": "staff"
    }
  ],
  "messages": [],
  "conflicts": [],
  "warnings": [],
  "generated_at": "…"
}
```

`complete: false` with `warnings` means the board read was partial — coordination is degraded, not the
repository free.

### `GET /api/coordination/inbox?session=&repo=`

`{available, complete, messages, conflicts, warnings, generated_at}` for `session` (`repo` defaults to
Repository_Management; the board is shared).

### `POST /api/coordination/presence`

Body `{agent?, session, repo, issue, branch, paths[], goals{}, ttl_hours}` → RM `register`. Renew before the
TTL expires. Returns the RM receipt `{ok, receipt, event, generated_at}`.

### `POST /api/coordination/presence/release`

Body `{session, repo}` → RM `release`.

### `POST /api/coordination/messages`

Body `{session, repo, to, text}` → RM `send` (`to` is a session id or `*`). Peer messages are untrusted data.
RM silently drops messages from a session without presence, so the sender must hold live presence in the same
`repo` in a fresh (uncached) board read: otherwise **409** `detail: {error, guidance: "register presence first
..."}`; a board that cannot be read is a 502.

### `POST /api/coordination/messages/ack`

Body `{session, repo, message_id}` → RM `ack`. Acknowledgement means receipt, not agreement. Same presence
precondition (409) as `messages`.

### `GET /api/coordination/claims?repo=&issue=`

RM `check_agent_claim`: `{available, held, agent, session, reason, expires_at, generated_at}`. RM fails open
(`held: false, reason: "error:<Exception>"`) when GitHub cannot be read; the API reports that as
`available: false`, never as a free issue. `held: true` may carry an empty `agent` (`do-not-automate` label, an
open PR or a branch referencing the issue).

### `POST /api/coordination/claims`

Body `{repo, issue, agent?, session, intent}`. Check and post run under one in-process lock per `repo#issue`.
**409** with `detail: {error, held_by, reason, expires_at, guidance}` whenever the issue is held and the holder
is not this agent and session: an empty holder, another agent, or this agent in another session. Renewal is
allowed only when RM reports the lease's `session` and it matches; RM's `check_agent_claim` does not report it
today, so a held issue is a 409 even for its own agent. Otherwise runs RM `post_agent_lease` and returns
`{claimed: true, previous, lease, warnings, generated_at}`. RM reports `errors: [...]`: when the lease comment
was posted but e.g. the `claim:*` label failed, the response is 200 with those errors in `warnings`; when no
comment was posted it is a 502 with the errors joined. A check that cannot answer (including fail-open) is a 502.

### `POST /api/coordination/claims/release`

Body `{repo, issue, agent?, session, reason}` → RM `release_agent_lease` →
`{released: true, result, warnings, generated_at}`. Same partial-outcome rule as claims: release comment posted
→ 200 with `warnings`; nothing posted (e.g. RM "Release requires the current lease's exact agent and session")
→ 502 with RM's errors.

### Status codes for writes

| Code | When                                                                                                         |
| ---- | ------------------------------------------------------------------------------------------------------------ |
| 401  | No credentials.                                                                                              |
| 403  | No `coordination.write` scope, missing CSRF header, or a bot acting as another agent / on another's session. |
| 409  | Claim held by anyone but this agent+session; message/ack from a session without live presence in `repo`.     |
| 422  | Body contract violated, `agent` missing (non-bot caller) or not in the RM roster.                            |
| 502  | RM script failed, the board could not be read, or `check_agent_claim` failed open.                           |

### `GET /api/coordination/briefing?repo=&agent=`

The one call an agent makes before starting work (`repo` optional; without it sessions and staff runs cover
every repository):

```json
{
  "repo": "Tools",
  "agent": "codex",
  "priorities": [],
  "holds": [
    {
      "id": "hold-…",
      "text": "no bulk stale-queue cancel",
      "applies_to": ["*"]
    }
  ],
  "sessions": [],
  "board_available": true,
  "staff_runs": [],
  "claims_hint": "Before editing: GET /api/coordination/claims …",
  "rules": ["Fleet rules: …"],
  "endpoints": { "claims": "GET|POST /api/coordination/claims" },
  "warnings": [],
  "generated_at": "…"
}
```

- `priorities` comes from `priorities.service.top_priorities(5)` when the Priorities module is installed on
  this node, else `[]`; a failure there adds a warning and never fails the briefing.
- `holds` are the active Staff Hub holds (`/api/staff/holds`).
- `rules` carries `staff.workspace.FLEET_RULES`, the guardrails every staff prompt ends with.

## Configuration

| Variable                  | Meaning                                                                       |
| ------------------------- | ----------------------------------------------------------------------------- |
| `STAFF_RM_ROOT`           | Repository_Management checkout (must contain `scripts/check_agent_claim.py`). |
| `STAFF_RM_PYTHON`         | Interpreter for the RM scripts (default `python3`).                           |
| `COORDINATION_REPOS`      | Comma-separated repos for the per-repo fallback read.                         |
| `DASHBOARD_LOOPBACK_AUTH` | `1` lets the node's own loopback Conductor write without a token.             |
