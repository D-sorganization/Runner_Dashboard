"""Auth scopes for generated MCP tool inventory (docs only; the hub enforces scopes on each route)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fleet_tools import Command

_SCOPE_IN_DESCRIPTION = re.compile(
    r"(?:requires|needs)(?:\s+scope)?\s+([\w]+(?:\.[\w]+)+)",
    re.IGNORECASE,
)

METHOD_AUTH_SCOPES: dict[str, str] = {
    "memory_context_pack": "memory.read",
    "memory_list": "memory.read",
    "memory_append": "memory.write",
    "memory_brief": "memory.read",
    "memory_append_session_digest": "memory.shared.summarize",
    "briefing": "fleet.read",
    "activity_context": "activity.read",
    "tail_session": "activity.read",
    "sessions": "fleet.read",
    "inbox": "fleet.read",
    "register_presence": "coordination.write",
    "release_presence": "coordination.write",
    "send_message": "coordination.write",
    "ack": "coordination.write",
    "check_claim": "coordination.write",
    "claim": "coordination.write",
    "release_claim": "coordination.write",
    "priorities": "fleet.read",
    "directives": "fleet.read",
    "staff_summary": "staff.read",
    "staff_roster": "staff.read",
    "run": "staff.read",
    "dispatch": "staff.dispatch",
    "cancel": "staff.cancel",
    "staff_threads_list": "staff.read",
    "staff_thread_open": "staff.chat",
    "staff_message_send": "staff.chat",
    "staff_thread_read": "staff.read",
    "staff_thread_wait": "staff.read",
    "staff_work_items": "staff.read",
    "staff_approvals_list": "staff.read",
    "staff_approval_decide": "staff.approve",
    "submit_proposal": "proposals.write",
    "list_proposals": "fleet.read",
    "fleet_query": "fleet.read",
    "barb_seat_status": "staff.read",
    "barb_seat_checkout": "staff.dispatch",
    "barb_seat_heartbeat": "staff.dispatch",
    "barb_seat_turnover": "staff.dispatch",
    "barb_seat_release": "staff.dispatch",
    "fleet_set_runner_target": "fleet.control",
    "friction_list": "fleet.read",
    "friction_report": "coordination.write",
    "owner_question_ask": "coordination.write",
    "owner_questions_list": "fleet.read",
    "exclusions_list": "staff.read",
    "exclusion_add": "staff.holds.write",
    "exclusion_remove": "staff.holds.write",
    "panel_presets": "staff.read",
    "panel_list": "staff.read",
    "panel_status": "staff.read",
    "panel_digest": "staff.read",
}


def auth_scope_for_fleet_tool(cmd: Command) -> str:
    """Return the documented auth scope for one fleet MCP tool."""
    if not cmd.tool:
        raise ValueError(f"{cmd.cli} is not an MCP tool")
    match = _SCOPE_IN_DESCRIPTION.search(cmd.description)
    if match:
        return match.group(1)
    try:
        return METHOD_AUTH_SCOPES[cmd.method]
    except KeyError as exc:
        raise KeyError(f"no auth scope mapping for MCP tool {cmd.tool!r} (method {cmd.method!r})") from exc
