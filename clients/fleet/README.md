# Fleet API Clients

These are stdlib-only clients (Python 3.10 or newer, no dependencies) for the Runner
Dashboard Fleet API: staff, coordination and priorities (epic #1192, #1228).

| File              | What it is                                                                           |
| ----------------- | ------------------------------------------------------------------------------------ |
| `fleet_client.py` | `FleetClient`: one validated method per endpoint; raises `FleetAPIError`.            |
| `fleet_tools.py`  | The command table (method, JSON Schema, CLI name, MCP tool name) shared by both.     |
| `fleetctl.py`       | The CLI. Prints JSON; exits 0 on success, 1 on an API error, 2 on bad arguments.                                       |
| `fleet_mcp.py`      | MCP stdio server (JSON-RPC 2.0, protocol `2025-06-18`); tool list in [MCP tool inventory](#mcp-tool-inventory) below.  |
| `rd_threads_mcp.py` | Thin stdio MCP (`scripts/rd_threads_mcp.py`) with four thread envelope tools; inventory in the same section.           |

```bash
export FLEET_API_URL=http://hub-a:8321 FLEET_API_TOKEN=svc_... FLEET_AGENT=claude
python3 clients/fleet/fleetctl.py briefing --repo Runner_Dashboard
python3 clients/fleet/fleetctl.py dispatch night-watch --repo Runner_Dashboard --prompt "sweep" --dry-run
```

```python
import sys; sys.path.insert(0, "clients/fleet")
from fleet_client import FleetClient, FleetAPIError

client = FleetClient()  # reads the FLEET_* environment variables
try:
    # No session given: derived as <agent>-<host>-<YYYYMMDD>; an explicit one must start with "<agent>-".
    client.claim("Runner_Dashboard", 1228, intent="fleet clients")
except FleetAPIError as exc:
    if exc.status == 409:
        ...  # another agent holds the issue, so pick other work
```

## Read-Only Library Commands

```bash
python3 clients/fleet/fleetctl.py library sources
python3 clients/fleet/fleetctl.py library search "energy transfer" --k 3 --run-id run-123
python3 clients/fleet/fleetctl.py library search "energy transfer" --scope local
python3 clients/fleet/fleetctl.py library fetch '[ref:hub-a:shared:w1:p3#sha256=1234abcd:p.3-3]' --run-id run-123
python3 clients/fleet/fleetctl.py library fetch desk-x9-library 42 --page 3
python3 clients/fleet/fleetctl.py library fetch desk-x9-library 42 --page 3 --scope federated --run-id run-123
python3 clients/fleet/fleetctl.py library verify --file docs/report.md --run-id run-123
```

Library search omits scope by default, preserving federated shared-only behavior. Pass
`--scope local` to search private local material, or `--scope federated` explicitly.
Fetch accepts either the canonical citation returned by search or a source id, work id,
and one-based page locator. The citation example is illustrative; use a real search
result. Locator fetch defaults to local scope so a private local source can be read
without contacting peers; add `--scope federated` to search registered peers for shared
sources, or `--node NAME` to select one registered peer. Responses preserve the canonical
citation and digest/node/source metadata, with passage text capped at 400 UTF-8 bytes.
Run IDs must belong to the authenticated caller; when omitted, the client uses
`STAFF_RUN_ID` if set. Source discovery reports local and pooled availability using
existing permissions and does not mutate the registry. Citation fetch continues through
the unified query implementation; locator fetch calls the same bounded, read-only API.
Existing flat commands remain available.

Verification reads a UTF-8 file (up to 1,000,000 characters) and sends its content to
the existing verification API. It prints JSON and exits 0 for verified content,
1 for invalid references or API failures, and 2 for invalid local input. The optional
run ID uses the same ownership checks and `STAFF_RUN_ID` default. The Python client
also exposes `library_verify(content, run_id=None)`; the shared command accepts
content rather than granting a tool access to local files.

For setup in each agent (Claude Code, Codex, Gemini CLI and Grok Bot), token minting and
the recommended agent loop, see [`docs/agents/connect.md`](../../docs/agents/connect.md).

<!-- BEGIN GENERATED: mcp-tool-inventory -->

## MCP tool inventory

Generated from `clients/fleet/fleet_tools.py` and `scripts/rd_threads_mcp.py`; run `python -m scripts.render_mcp_tool_inventory --write` after changing tools.

### `fleet_mcp.py` (`clients/fleet/fleet_mcp.py`)

| MCP tool                             | Description                                                                                                                                                  | Auth scope                |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------- |
| `fleet_memory_context_pack`          | Read the shared memory context pack for this authenticated principal.                                                                                        | `memory.read`             |
| `fleet_memory_list`                  | List shared memory entries with optional filters (requires memory.read scope).                                                                               | `memory.read`             |
| `fleet_memory_append`                | Append a shared memory entry (requires memory.write scope).                                                                                                  | `memory.write`            |
| `fleet_memory_brief`                 | START of a Barb session: promoted/accepted lessons, recent Barb turnover and latest memory entries for a scope, as one compact size-capped text block.…      | `memory.read`             |
| `fleet_memory_append_session_digest` | Append a short, transcript-free summary for one Codex or Dashboard conversation turn (requires memory.shared.summarize; retries of the same turn are…        | `memory.shared.summarize` |
| `fleet_briefing`                     | START HERE before any work: priorities, directives, holds, who is working in the repo, staff runs, claim hints and the fleet rules - one call.               | `fleet.read`              |
| `activity_context`                   | Read token-capped shared running activity context (needs scope activity.read).                                                                               | `activity.read`           |
| `tail_session`                       | Tail bounded session transcript chunks after a sequence cursor (needs scope activity.read).                                                                  | `activity.read`           |
| `fleet_sessions`                     | Active agent sessions (all vendors, from the coordination board) plus in-flight staff runs.                                                                  | `fleet.read`              |
| `fleet_inbox`                        | Messages addressed to your session and detected path/issue conflicts.                                                                                        | `fleet.read`              |
| `fleet_register_presence`            | Announce what you are working on (repo, issue, branch, paths) so other agents avoid collisions.                                                              | `coordination.write`      |
| `fleet_release_presence`             | Remove your presence entry when you finish.                                                                                                                  | `coordination.write`      |
| `fleet_send_message`                 | Send a coordination message to another session, or to every session in the repo with to='*' (posted to the fleet board). Register presence first: the board… | `coordination.write`      |
| `fleet_ack_message`                  | Acknowledge a message from your inbox (confirms receipt, not agreement) so it stops being re-delivered.                                                      | `coordination.write`      |
| `fleet_check_claim`                  | Check whether an issue is leased by another agent: {held, agent, reason, expires_at}.                                                                        | `coordination.write`      |
| `fleet_claim_issue`                  | Lease an issue before working on it. Fails with status 409 when another agent holds it - pick other work.                                                    | `coordination.write`      |
| `fleet_release_claim`                | Release your lease on an issue (after the PR is opened, or when abandoning).                                                                                 | `coordination.write`      |
| `fleet_priorities`                   | Current board-meeting priorities (ranked active items, deferred, disagreements), directives, portfolios.                                                     | `fleet.read`              |
| `fleet_directives`                   | Operator directives: short, active focus statements that override default prioritisation.                                                                    | `fleet.read`              |
| `fleet_staff_summary`                | Staff Hub summary: roles, in-flight runs, today's counts and spend.                                                                                          | `staff.read`              |
| `fleet_staff_roster`                 | Staff roles (name, purpose, providers, schedule) available for dispatch.                                                                                     | `staff.read`              |
| `fleet_run_status`                   | One staff run with its recent events.                                                                                                                        | `staff.read`              |
| `fleet_dispatch_role`                | Dispatch a staff role run (or preview it with dry_run). One of issue, pr or prompt is required.                                                              | `staff.dispatch`          |
| `staff_run_cancel`                   | Cancel a staff run.                                                                                                                                          | `staff.cancel`            |
| `staff_threads_list`                 | List conversation threads with optional participant, status, unread, cursor.                                                                                 | `staff.read`              |
| `staff_thread_open`                  | Open a conversation thread with a staff role (default 'auto' routes to Barb).                                                                                | `staff.chat`              |
| `staff_message_send`                 | Send an idempotent message to a conversation thread.                                                                                                         | `staff.chat`              |
| `staff_thread_read`                  | Read messages and details from a conversation thread since a sequence number.                                                                                | `staff.read`              |
| `staff_thread_wait`                  | Long-poll up to timeout seconds (max 60) for a reply in a conversation thread.                                                                               | `staff.read`              |
| `staff_work_items`                   | List and filter tracked work items across the fleet.                                                                                                         | `staff.read`              |
| `staff_approvals_list`               | List action proposals awaiting review or in terminal states.                                                                                                 | `staff.read`              |
| `staff_approval_decide`              | Decide (approve or deny) an action proposal (requires staff.approve scope).                                                                                  | `staff.approve`           |
| `submit_proposal`                    | Submit a proposal to the Board (stored in Repository_Management labelled board:proposal).                                                                    | `proposals.write`         |
| `list_proposals`                     | List board proposals with decision labels, meeting consensus links, and outcome badges.                                                                      | `fleet.read`              |
| `fleet_query`                        | Consolidated fleet on-request context query tool across context, library, knowledge, memory, and codebase.                                                   | `fleet.read`              |
| `fleet_barb_status`                  | Barb continuity: authority, scoped seat leases (who holds what until when), recent turnover notes and the task inventory. Read the last turnover before…     | `staff.read`              |
| `fleet_barb_checkout`                | Take a Barb seat for a scope. 409 = held by another agent until a stated time: do not act, pick another scope. Keep the returned epoch for heartbeat,…       | `staff.dispatch`          |
| `fleet_barb_heartbeat`               | Extend your seat lease; call at least every TTL/3. 404 means the hub predates heartbeat support.                                                             | `staff.dispatch`          |
| `fleet_barb_turnover`                | Write your shift turnover (summary and next actions) for the next holder. Required before release.                                                           | `staff.dispatch`          |
| `fleet_set_runner_target`            | Set an expiring runner-count override on a fleet host (schedule resumes after until).                                                                        | `fleet.control`           |
| `fleet_barb_release`                 | Release your seat after writing a turnover; the hub refuses (409) without one.                                                                               | `staff.dispatch`          |
| `fleet_friction_list`                | List friction ledger rows (a row closes only with a merged fix and a detector).                                                                              | `fleet.read`              |
| `fleet_friction_report`              | Report fleet friction: pass friction_id to record a recurrence, else title and seen to create a row.                                                         | `coordination.write`      |
| `fleet_owner_question_ask`           | Queue a question for the owner with optional options and a recommendation.                                                                                   | `coordination.write`      |
| `fleet_owner_questions_list`         | List owner questions (status open, answered or withdrawn).                                                                                                   | `fleet.read`              |
| `fleet_exclusions_list`              | List dispatch exclusions (needs scope staff.read).                                                                                                           | `staff.read`              |
| `fleet_exclusion_add`                | Exclude a repo, issue or path glob from automated dispatch; a reason is required (needs scope staff.holds.write).                                            | `staff.holds.write`       |
| `fleet_exclusion_remove`             | Lift a dispatch exclusion by id (needs scope staff.holds.write).                                                                                             | `staff.holds.write`       |
| `fleet_panel_presets`                | List expert-panel presets.                                                                                                                                   | `staff.read`              |
| `fleet_panel_list`                   | List recent expert panels.                                                                                                                                   | `staff.read`              |
| `fleet_panel_status`                 | Read a panel's turns, stances and synthesis.                                                                                                                 | `staff.read`              |
| `fleet_panel_digest`                 | Read a panel's compact verdict.                                                                                                                              | `staff.read`              |

### `rd_threads_mcp.py` (`scripts/rd_threads_mcp.py`)

| MCP tool   | Description                                                                                         | Auth scope   |
| ---------- | --------------------------------------------------------------------------------------------------- | ------------ |
| `send`     | Post an envelope to an agent's thread, or to='*' for the fleet board.                               | `staff.chat` |
| `inbox`    | Unread envelopes addressed to you (needs fleet.read).                                               | `fleet.read` |
| `peers`    | Live agent sessions (needs fleet.read).                                                             | `fleet.read` |
| `ask_barb` | Ask @barb; options [{label, recommended?}] with exactly one recommended plus recommendation_reason. | `staff.chat` |

<!-- END GENERATED: mcp-tool-inventory -->

To add an endpoint, add a method to `FleetClient` and one `Command` row to `fleet_tools.py`.
The CLI gets the new subcommand automatically. The endpoint also becomes an MCP tool if
the row sets `tool=`. Tests are in `tests/clients/`.

## barb-worker VM

Barb lane users on the barb-worker VM call the hub through per-user HTTP proxies; bootstrap and
lane systemd units live under `scripts/barb-worker/` (see [`docs/agents/barb-worker-vm.md`](../../docs/agents/barb-worker-vm.md)).
Set `FLEET_API_URL` / `BARB_WORKER_HUB_URL` to the hub’s HTTPS Tailscale origin (not `:8321`).
