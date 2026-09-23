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

from fleet_client import FleetArgumentError, FleetClient

REPO = {"type": "string", "description": "Repository name, e.g. Runner_Dashboard (owner/ prefix optional)."}
BARE_REPO = {"type": "string", "description": "Bare repository name, e.g. Runner_Dashboard."}
ISSUE = {"type": "integer", "minimum": 1, "description": "Issue number."}
SESSION = {"type": "string", "description": "Your session id (defaults to $FLEET_SESSION)."}
AGENT = {
    "type": "string",
    "description": "Your agent name, e.g. claude, codex, gemini, grok (defaults to $FLEET_AGENT).",
}
RUN_ID = {"type": "string", "description": "Staff run id, e.g. run-905a8b3586a7."}


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
            "branch": {"type": "string", "description": "Your working branch."},
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
        "Send a coordination message to another session or agent (posted to the fleet board).",
        {
            "repo": REPO,
            "to": {"type": "string", "description": "Recipient session id or agent name."},
            "text": {"type": "string", "maxLength": 4000, "description": "Message text."},
            "session": SESSION,
        },
        required=("repo", "to", "text"),
        tool="fleet_send_message",
    ),
    Command(
        "ack",
        "ack",
        "Acknowledge a received message.",
        {"repo": REPO, "message_id": {"type": "string", "description": "Message id."}, "session": SESSION},
        required=("repo", "message_id"),
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
            "intent": {"type": "string", "maxLength": 4000, "description": "What you intend to do."},
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
            "reason": {"type": "string", "maxLength": 4000, "description": "Why, e.g. 'PR #123 opened'."},
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
        "Replace the operator directive list (requires write auth).",
        {
            "directives": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "text": {"type": "string", "maxLength": 500},
                        "repo": {"type": "string", "description": "Repository or '*'."},
                        "priority": {"type": "integer", "minimum": 1, "maximum": 5},
                        "expires": {"type": "string", "description": "ISO-8601 expiry."},
                        "set_by": {"type": "string"},
                    },
                    "required": ["text"],
                },
            }
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
        "cancel", "cancel", "Cancel a staff run.", {"run_id": RUN_ID}, required=("run_id",), positional=("run_id",)
    ),
)

BY_CLI: dict[str, Command] = {c.cli: c for c in COMMANDS}
BY_TOOL: dict[str, Command] = {c.tool: c for c in COMMANDS if c.tool}
