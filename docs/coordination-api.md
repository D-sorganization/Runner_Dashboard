# Fleet Coordination API

Issue #1229, epic #1192. One HTTP surface on every dashboard node through which any agent — Claude Code,
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
  Repository_Management) and merges the results. Every write clears the cache.
- **Advisory presence, binding claims.** Presence is advisory. A claim held by another agent is a 409.

## Authentication

| Kind   | Dependency                    | Accepted callers                                                                                                                                      |
| ------ | ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Reads  | `require_fleet_peer`          | Any principal; the `HUB_FLEET_TOKEN` bearer when set; anyone on the tailnet when it is not set.                                                       |
| Writes | `require_coordination_writer` | A principal with scope `coordination.write` (presets `bot`, `operator`, `admin`), or the loopback orchestrator peer when `DASHBOARD_LOOPBACK_AUTH=1`. |

- The shared `HUB_FLEET_TOKEN` is **not** a write credential. Mint one bot service token per agent so every
  write is attributable.
- A principal without the scope gets 403; no credentials gets 401.
- Writes need the CSRF header `X-Requested-With: XMLHttpRequest`, bearer callers included.
- `agent` may be omitted from a write body when the caller is a bot principal: it defaults to the principal id.
  Otherwise a missing `agent` is a 422.
- `/api/coordination/` is in `_ALT_AUTH_EXEMPT_PREFIXES` (like `/api/staff/`) so fleet-token readers are not
  stopped by the structural perimeter; every route carries its own dependency.

## Request contracts

| Field       | Rule                                                                        |
| ----------- | --------------------------------------------------------------------------- |
| `repo`      | bare repository name `^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`, no `..`, no owner |
| `session`   | `^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$`                                      |
| `agent`     | `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`                                         |
| `issue`     | integer > 0                                                                 |
| `text`      | 1–4000 characters                                                           |
| `ttl_hours` | 0.1–8 (default 2)                                                           |
| `branch`    | `^[A-Za-z0-9][A-Za-z0-9._/@+-]{0,199}$`                                     |
| `paths`     | ≤ 50 entries, each ≤ 300 characters, not starting with `-`                  |
| `goals`     | ≤ 20 `key: outcome` pairs; keys 1–80 characters without `=`                 |
| `to`        | a session id or `*` (everyone in `repo`)                                    |
| `intent`    | `^[a-z][a-z-]{0,39}$` (default `implement`)                                 |

Unknown body fields are rejected (422).

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

### `POST /api/coordination/messages/ack`

Body `{session, repo, message_id}` → RM `ack`. Acknowledgement means receipt, not agreement.

### `GET /api/coordination/claims?repo=&issue=`

RM `check_agent_claim`: `{available, held, agent, reason, expires_at, generated_at}`.

### `POST /api/coordination/claims`

Body `{repo, issue, agent?, session, intent}`. Checks first; **409** with
`detail: {error, held_by, reason, expires_at, guidance}` when another agent holds the claim (the holder
renewing its own claim is allowed). Otherwise runs RM `post_agent_lease` and returns
`{claimed: true, previous, lease, generated_at}`. A check that cannot answer, or a lease the script did not
confirm, is a 502.

### `POST /api/coordination/claims/release`

Body `{repo, issue, agent?, session, reason}` → RM `release_agent_lease`.

### `GET /api/coordination/briefing?repo=&agent=`

The one call an agent makes before starting work:

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
