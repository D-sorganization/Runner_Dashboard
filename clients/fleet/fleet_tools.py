"""Single command table shared by ``fleetctl.py`` (CLI) and ``fleet_mcp.py`` (MCP tools).

Each :class:`Command` maps a CLI subcommand (and optionally an MCP tool name) to one
:class:`fleet_client.FleetClient` method plus the JSON Schema of its arguments. The CLI
derives its argparse options from the schema; the MCP server publishes the schema as the
tool's ``inputSchema``. Adding an endpoint means adding one row here (DRY).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fleet_client import LIMITS, FleetArgumentError, FleetClient

REPO = {"type": "string", "description": "Repository name, e.g. Runner_Dashboard (owner/ prefix optional)."}
BARE_REPO = {"type": "string", "description": "Bare repository name, e.g. Runner_Dashboard."}
ISSUE = {"type": "integer", "minimum": 1, "description": "Issue number."}
SESSION = {
    "type": "string",
    "description": "Your session id; must start with '<agent>-' (defaults to $FLEET_SESSION, else "
    "<agent>-<host>-<YYYYMMDD>).",
}
AGENT = {
    "type": "string",
    "description": "Your agent name, e.g. claude, codex, gemini, grok (defaults to $FLEET_AGENT); a bot token "
    "agent-<name> may only act as <name>.",
}
RUN_ID = {"type": "string", "description": "Staff run id, e.g. run-905a8b3586a7."}
THREAD_ID = {"type": "string", "description": "Conversation thread id, e.g. th_1234."}
PROPOSAL_ID = {"type": "string", "description": "Action proposal id, e.g. prop_1234."}


@dataclass(frozen=True)
class Command:
    """One client method exposed as a CLI subcommand and (when ``tool`` is set) an MCP tool."""

    cli: str
    method: str
    description: str
    properties: dict[str, dict[str, Any]] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    tool: str | None = None
    positional: tuple[str, ...] = ()

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": self.properties,
            "required": list(self.required),
            "additionalProperties": False,
        }

    def invoke(self, client: FleetClient, arguments: dict[str, Any]) -> Any:
        """Validate ``arguments`` against the schema's names and call the client method."""
        unknown = sorted(set(arguments) - set(self.properties))
        if unknown:
            raise FleetArgumentError(f"{self.cli}: unknown arguments {unknown}")
        missing = [name for name in self.required if arguments.get(name) in (None, "")]
        if missing:
            raise FleetArgumentError(f"{self.cli}: missing required arguments {missing}")
        method: Callable[..., Any] = getattr(client, self.method)
        return method(**{k: v for k, v in arguments.items() if v is not None})


COMMANDS: tuple[Command, ...] = (
    # ---------------------------------------------------------------- coordination
    Command(
        "briefing",
        "briefing",
        "START HERE before any work: priorities, directives, holds, who is working in the repo, staff runs, "
        "claim hints and the fleet rules - one call.",
        {"repo": REPO, "agent": AGENT},
        tool="fleet_briefing",
    ),
    Command(
        "sessions",
        "sessions",
        "Active agent sessions (all vendors, from the coordination board) plus in-flight staff runs.",
        {"repo": REPO},
        tool="fleet_sessions",
    ),
    Command(
        "inbox",
        "inbox",
        "Messages addressed to your session and detected path/issue conflicts.",
        {"session": SESSION},
        tool="fleet_inbox",
    ),
    Command(
        "register-presence",
        "register_presence",
        "Announce what you are working on (repo, issue, branch, paths) so other agents avoid collisions.",
        {
            "repo": REPO,
            "session": SESSION,
            "agent": AGENT,
            "issue": ISSUE,
            "branch": {"type": "string", "maxLength": 200, "description": "Your working branch, e.g. main."},
            "paths": {"type": "array", "items": {"type": "string"}, "description": "Paths you will edit."},
            "goals": {"type": "object", "description": "Free-form goals object."},
            "ttl_hours": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 8,
                "description": "Presence TTL (RM cap 8 h).",
            },
        },
        required=("repo", "issue", "branch"),
        tool="fleet_register_presence",
    ),
    Command(
        "release-presence",
        "release_presence",
        "Remove your presence entry when you finish.",
        {"repo": REPO, "session": SESSION},
        required=("repo",),
        tool="fleet_release_presence",
    ),
    Command(
        "send-message",
        "send_message",
        "Send a coordination message to another session, or to every session in the repo with to='*' "
        "(posted to the fleet board). Register presence first: the board drops messages from unknown sessions.",
        {
            "repo": REPO,
            "to": {
                "type": "string",
                "description": "Recipient session id (see fleet_sessions), or '*' for every session in the repo.",
            },
            "text": {"type": "string", "maxLength": LIMITS.max_message_text, "description": "Message text."},
            "session": SESSION,
        },
        required=("repo", "to", "text"),
        tool="fleet_send_message",
    ),
    Command(
        "ack",
        "ack",
        "Acknowledge a message from your inbox (confirms receipt, not agreement) so it stops being re-delivered.",
        {
            "repo": REPO,
            "message_id": {"type": "string", "description": "Message id from fleet_inbox."},
            "session": SESSION,
        },
        required=("repo", "message_id"),
        tool="fleet_ack_message",
    ),
    Command(
        "check-claim",
        "check_claim",
        "Check whether an issue is leased by another agent: {held, agent, reason, expires_at}.",
        {"repo": REPO, "issue": ISSUE},
        required=("repo", "issue"),
        tool="fleet_check_claim",
    ),
    Command(
        "claim",
        "claim",
        "Lease an issue before working on it. Fails with status 409 when another agent holds it - pick other work.",
        {
            "repo": REPO,
            "issue": ISSUE,
            "intent": {
                "type": "string",
                "maxLength": LIMITS.max_intent,
                "description": "One line: what you intend to do (server default 'implement').",
            },
            "agent": AGENT,
            "session": SESSION,
        },
        required=("repo", "issue"),
        tool="fleet_claim_issue",
    ),
    Command(
        "release-claim",
        "release_claim",
        "Release your lease on an issue (after the PR is opened, or when abandoning).",
        {
            "repo": REPO,
            "issue": ISSUE,
            "reason": {
                "type": "string",
                "maxLength": LIMITS.max_reason,
                "description": "One line: why, e.g. 'PR #123 opened' (server default 'work completed').",
            },
            "agent": AGENT,
            "session": SESSION,
        },
        required=("repo", "issue"),
        tool="fleet_release_claim",
    ),
    # ---------------------------------------------------------------- priorities
    Command(
        "priorities",
        "priorities",
        "Current board-meeting priorities (ranked active items, deferred, disagreements), directives, portfolios.",
        tool="fleet_priorities",
    ),
    Command("meetings", "meetings", "List board-meeting dates and which files each has."),
    Command(
        "meeting",
        "meeting",
        "One board meeting: parsed consensus plus raw packet/instructions markdown.",
        {"date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "description": "Meeting date YYYY-MM-DD."}},
        required=("date",),
        positional=("date",),
    ),
    Command(
        "directives",
        "directives",
        "Operator directives: short, active focus statements that override default prioritisation.",
        tool="fleet_directives",
    ),
    Command(
        "set-directives",
        "set_directives",
        "Replace the operator directive list (operator credentials: priorities.write; set_by is the caller).",
        {
            "directives": {
                "type": "array",
                "maxItems": LIMITS.max_directives,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "text": {"type": "string", "maxLength": LIMITS.max_directive_text, "description": "One line."},
                        "repo": {"type": "string", "description": "Bare repository name or '*'."},
                        "priority": {"type": "integer", "minimum": 1, "maximum": 5},
                        "expires": {"type": "string", "description": "ISO-8601 expiry."},
                    },
                    "required": ["text"],
                },
            },
            "version": {
                "type": "string",
                "description": "'version' from the directives read you edited; the server answers 409 if it changed.",
            },
        },
        required=("directives",),
    ),
    # ---------------------------------------------------------------- staff
    Command(
        "staff-summary",
        "staff_summary",
        "Staff Hub summary: roles, in-flight runs, today's counts and spend.",
        tool="fleet_staff_summary",
    ),
    Command(
        "staff-roster",
        "staff_roster",
        "Staff roles (name, purpose, providers, schedule) available for dispatch.",
        tool="fleet_staff_roster",
    ),
    Command(
        "staff-board",
        "staff_board",
        "Fleet staff board (per machine).",
        {"local": {"type": "boolean", "description": "Only this node."}},
    ),
    Command(
        "staff-runs",
        "staff_runs",
        "Recent staff runs.",
        {
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "role": {"type": "string"},
            "status": {"type": "string"},
            "since": {"type": "string", "description": "ISO date/time."},
        },
    ),
    Command(
        "run",
        "run",
        "One staff run with its recent events.",
        {"run_id": RUN_ID, "events": {"type": "integer", "minimum": 0, "maximum": 500}},
        required=("run_id",),
        tool="fleet_run_status",
        positional=("run_id",),
    ),
    Command("schedule", "staff_schedule", "Staff schedule: run windows, next runs, budgets."),
    Command("holds", "holds", "Operator holds (work that must not be started)."),
    Command(
        "usage",
        "usage",
        "Staff usage/spend.",
        {"since": {"type": "string"}, "group": {"type": "string", "enum": ["provider", "role", "day"]}},
    ),
    Command(
        "dispatch",
        "dispatch",
        "Dispatch a staff role run (or preview it with dry_run). One of issue, pr or prompt is required.",
        {
            "role": {"type": "string", "description": "Staff role name, e.g. night-watch."},
            "repo": BARE_REPO,
            "issue": ISSUE,
            "pr": {"type": "integer", "minimum": 1, "description": "Pull request number."},
            "prompt": {"type": "string", "maxLength": 20000},
            "provider": {"type": "string", "description": "Provider override, e.g. claude, codex."},
            "model": {"type": "string"},
            "machine": {"type": "string", "description": "'auto' (default), 'local' or a node name."},
            "dry_run": {"type": "boolean", "description": "Preview the plan without running it."},
        },
        required=("role",),
        tool="fleet_dispatch_role",
        positional=("role",),
    ),
    Command(
        "cancel",
        "cancel",
        "Cancel a staff run.",
        {"run_id": RUN_ID},
        required=("run_id",),
        tool="staff_run_cancel",
        positional=("run_id",),
    ),
    Command(
        "staff-threads",
        "staff_threads_list",
        "List conversation threads with optional participant, status, unread, cursor.",
        {
            "participant": {"type": "string", "description": "Filter by participant role or agent name."},
            "status": {"type": "string", "enum": ["open", "archived"], "description": "Filter by status."},
            "unread_by": {"type": "string", "description": "Filter threads with unread messages."},
            "cursor": {"type": "string", "description": "Pagination cursor."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Max threads to return."},
        },
        tool="staff_threads_list",
    ),
    Command(
        "thread-open",
        "staff_thread_open",
        "Open a conversation thread with a staff role (default 'auto' routes to Barb).",
        {
            "role": {"type": "string", "description": "Role to converse with ('auto' or role name, default 'auto')."},
            "title": {"type": "string", "description": "Thread title."},
            "initial_message": {"type": "string", "description": "Optional first message to post into the thread."},
            "kind": {"type": "string", "enum": ["direct", "group", "auto"], "description": "Thread kind."},
            "project_id": {"type": "string", "description": "Optional project id."},
        },
        tool="staff_thread_open",
    ),
    Command(
        "message-send",
        "staff_message_send",
        "Send an idempotent message to a conversation thread.",
        {
            "thread_id": THREAD_ID,
            "body": {"type": "string", "description": "Message content."},
            "idempotency_key": {"type": "string", "description": "Idempotency key (auto-generated if omitted)."},
        },
        required=("thread_id", "body"),
        tool="staff_message_send",
        positional=("thread_id", "body"),
    ),
    Command(
        "thread-read",
        "staff_thread_read",
        "Read messages and details from a conversation thread since a sequence number.",
        {
            "thread_id": THREAD_ID,
            "since_seq": {
                "type": "integer",
                "minimum": 0,
                "description": "Return messages after this sequence number.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Max messages to return."},
        },
        required=("thread_id",),
        tool="staff_thread_read",
        positional=("thread_id",),
    ),
    Command(
        "thread-wait",
        "staff_thread_wait",
        "Long-poll up to timeout seconds (max 60) for a reply in a conversation thread.",
        {
            "thread_id": THREAD_ID,
            "since_seq": {
                "type": "integer",
                "minimum": 0,
                "description": "Wait for messages after this sequence number.",
            },
            "timeout": {
                "type": "number",
                "minimum": 1.0,
                "maximum": 60.0,
                "description": "Timeout in seconds (max 60).",
            },
        },
        required=("thread_id",),
        tool="staff_thread_wait",
        positional=("thread_id",),
    ),
    Command(
        "work-items",
        "staff_work_items",
        "List and filter tracked work items across the fleet.",
        {
            "mine": {"type": "boolean", "description": "Filter work items requested by caller."},
            "overdue": {"type": "boolean", "description": "Filter work items past SLA deadline."},
            "waiting_on_me": {"type": "boolean", "description": "Filter work items waiting on caller action."},
            "state": {
                "type": "string",
                "description": (
                    "Filter by state: open, in_progress, waiting_on_user, waiting_on_ci, "
                    "blocked, done, cancelled, escalated."
                ),
            },
            "thread_id": {"type": "string", "description": "Filter by associated thread id."},
            "cursor": {"type": "string", "description": "Pagination cursor."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Max items to return."},
        },
        tool="staff_work_items",
    ),
    Command(
        "approvals",
        "staff_approvals_list",
        "List action proposals awaiting review or in terminal states.",
        {
            "thread_id": {"type": "string", "description": "Filter proposals by thread id."},
            "state": {
                "type": "string",
                "enum": ["proposed", "approved", "denied", "executing", "done", "failed", "expired"],
                "description": "Filter by proposal state.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Max proposals to return."},
        },
        tool="staff_approvals_list",
    ),
    Command(
        "approval-decide",
        "staff_approval_decide",
        "Decide (approve or deny) an action proposal (requires staff.approve scope).",
        {
            "proposal_id": PROPOSAL_ID,
            "decision": {
                "type": "string",
                "enum": ["approved", "denied"],
                "description": "Decision: 'approved' or 'denied'.",
            },
            "reason": {"type": "string", "description": "Optional rationale for the decision."},
        },
        required=("proposal_id", "decision"),
        tool="staff_approval_decide",
        positional=("proposal_id", "decision"),
    ),
    Command(
        "submit-proposal",
        "submit_proposal",
        "Submit a proposal to the Board (stored in Repository_Management labelled board:proposal).",
        {
            "title": {"type": "string", "description": "Proposal title (e.g. 'Adopt WebGPU for Visualization')."},
            "target_repos": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Target repo(s), e.g. ['Runner_Dashboard'].",
            },
            "problem": {"type": "string", "description": "Problem statement explaining why this is needed."},
            "evidence": {"type": "string", "description": "Evidence, benchmarks, or incident links supporting this."},
            "options_considered": {
                "type": "string",
                "description": "Options considered and pros/cons evaluated.",
            },
            "lean": {"type": "string", "description": "The submitter's recommended option / lean."},
            "estimated_cost": {
                "type": "string",
                "enum": ["Low", "Medium", "High"],
                "description": "Estimated effort if accepted.",
            },
            "urgency": {
                "type": "string",
                "enum": ["Routine", "Urgent", "Emergency"],
                "description": "When a Board decision is needed.",
            },
            "source": {"type": "string", "description": "Submitter identifier (defaults to agent name)."},
            "code_request_url": {"type": "string", "description": "Optional link to an originating Code Request."},
            "confirm_not_duplicate": {
                "type": "boolean",
                "description": "Confirm submission even if potential duplicate proposals exist.",
            },
        },
        required=(
            "title",
            "target_repos",
            "problem",
            "evidence",
            "options_considered",
            "lean",
            "estimated_cost",
            "urgency",
        ),
        tool="submit_proposal",
    ),
    Command(
        "list-proposals",
        "list_proposals",
        "List board proposals with decision labels, meeting consensus links, and outcome badges.",
        {
            "state": {
                "type": "string",
                "enum": ["open", "decided"],
                "description": "Filter proposals by state (open or decided).",
            },
            "repo": REPO,
        },
        tool="list_proposals",
    ),
)

BY_CLI: dict[str, Command] = {c.cli: c for c in COMMANDS}
BY_TOOL: dict[str, Command] = {c.tool: c for c in COMMANDS if c.tool}
