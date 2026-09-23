# Priorities API

Part of the Fleet Coordination API (epic #1192, issue #1227). One HTTP surface
on any dashboard node that tells an agent — Claude Code, Codex, Gemini CLI,
Grok Bot, a staff run or a human — what the fleet should work on.

## Sources

| Source         | Where                                                                                      | Owner                 |
| -------------- | ------------------------------------------------------------------------------------------ | --------------------- |
| Board meetings | RM `docs/board-meetings/<YYYY-MM-DD>/{packet,consensus,instructions,pathway-log-entry}.md` | Repository_Management |
| Portfolios     | RM `config/fleet_manifest.yaml` → `portfolios.<name>`                                      | Repository_Management |
| Directives     | `staff_directives.json` in the dashboard config dir (`STAFF_DIRECTIVES_FILE` overrides)    | this dashboard        |

The RM checkout is found exactly like the Staff Hub finds it
(`staff.workspace.rm_root()`: `STAFF_RM_ROOT`, else `Repository_Management`
under the repos roots). RM files are read; RM code is never imported.

`consensus.md` follows RM `docs/templates/board-consensus.md`. The parser
(`backend/priorities/consensus.py`) reads the meeting metadata table, the
Borda table, `### Active Priorities` (numbered `**item** — project — scope`
lines with `Assigned to:` / `Epic/Issue:` / `Acceptance criteria:`
sub-bullets), the `### Deferred Backlog` table and `## Disagreement Flags`.
It is tolerant of the template's malformed separator rows (wrong column
counts) and of rows with extra or missing cells, and it drops unfilled
placeholders (`<item>`, `YYYY-MM-DD`, `...`).

## Endpoints

All reads return HTTP 200 and carry `generated_at`. When the RM checkout or a
meeting is missing they answer `{"available": false, "reason": "..."}` instead
of an error.

| Method | Path                              | Auth                           | Returns                                                                                                                                                                                                                                      |
| ------ | --------------------------------- | ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/priorities`                 | fleet peer                     | `{available, reason?, board:{date, metadata, active[], deferred[], borda[], disagreements[]} \| null, directives[], portfolios[], portfolios_reason?, generated_at}`. `board` comes from the newest dated meeting that has a `consensus.md`. |
| GET    | `/api/priorities/meetings`        | fleet peer                     | `{available, meetings:[{date, files[], has_consensus}], generated_at}`, newest first.                                                                                                                                                        |
| GET    | `/api/priorities/meetings/{date}` | fleet peer                     | Parsed `consensus` plus raw `consensus_markdown`, `packet`, `instructions`, `pathway_log_entry` (null when absent). `422` for a malformed or impossible date, `404` for a date with no folder.                                               |
| GET    | `/api/priorities/directives`      | fleet peer                     | `{directives[], version}`, unexpired only, priority 1 first. `version` fingerprints the stored list; the storage path is never returned.                                                                                                     |
| PUT    | `/api/priorities/directives`      | `priorities.write` or loopback | Replaces the list; returns `{directives[], version}`. `409` when the body's `version` is stale.                                                                                                                                              |

Active item: `{rank, item, project, scope, assigned_to, tracking, acceptance}`.
Deferred item: `{item, project, reason, reassess}`. Borda row:
`{rank, item, project, score, votes}`. Portfolio:
`{name, description, wip_limit, review_cadence, unattended_agents[], repos[]}`.

### Directives

```json
{
  "directives": [
    {
      "text": "Finish the coordination API first",
      "repo": "Runner_Dashboard",
      "priority": 1,
      "expires": "2026-09-30T00:00:00Z"
    }
  ],
  "version": "<version from the GET>"
}
```

- `text` 1–500 characters on **one line** (CR/LF is rejected with 422): every
  directive is pasted into every staff prompt as a single bullet. Put
  checklists in the tracking issue. `repo` is `*` (default) or a bare
  repository name.
- `priority` 1 (highest) to 5, default 3.
- `expires` optional ISO-8601; stored as UTC `...Z`. Expired directives are
  filtered from every read and dropped on the next PUT.
- `set_by` is always set by the server: the authenticated caller (`principal:<id>` or
  `loopback-dev`) for new or changed directives, the stored author for unchanged ones.
  A `set_by` in the body is ignored.
- `version` (optional) is the value from the GET you edited. When another writer saved
  in between, the PUT answers `409` and writes nothing; reload and re-apply. Omit it only
  for scripted, single-writer updates.
- A stored entry that no longer validates is skipped (and logged) on read; the other
  directives are kept.
- `id` defaults to a hash of text and repo; duplicate ids are rejected (422).

### Auth

- Reads: `require_fleet_peer` — an operator principal or, when
  `HUB_FLEET_TOKEN` is set, `Authorization: Bearer <HUB_FLEET_TOKEN>`.
- PUT: `priorities.auth.require_priorities_writer` — a principal whose roles grant
  `priorities.write` (`operator` preset, `admin` via `*`; `bot`, `viewer` and other
  principals get 403), or a loopback caller when `DASHBOARD_LOOPBACK_AUTH=1`.
  Directives steer every staff prompt, so agent bot tokens (which hold
  `coordination.write`) cannot set them (#1243). The shared fleet token alone is
  not accepted for writes. Every write needs `X-Requested-With: XMLHttpRequest`
  (bearer callers too).
- The exact path `/api/priorities` and the prefix `/api/priorities/` are exempt
  from the structural perimeter (`_ALT_AUTH_EXEMPT_EXACT` /
  `_ALT_AUTH_EXEMPT_PREFIXES`) so fleet-token readers are not stopped by it; the
  route dependencies above are the gate. `/api/prioritiesX` is not exempt.

## For other modules

`priorities.service.top_priorities(limit) -> list[dict]` returns the active board
items (by rank, `kind: "board"`) followed by active directives (by priority,
`kind: "directive"`), truncated to `limit`. It never raises for a missing RM
checkout; the coordination briefing uses it.

```bash
curl -s -H "Authorization: Bearer $HUB_FLEET_TOKEN" http://127.0.0.1:8321/api/priorities | jq '.board.active'
```
