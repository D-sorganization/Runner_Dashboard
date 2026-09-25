"""Structured reply contract parser for conversational staff turns (SC-B5, #1308).

A conversational role reply is plain markdown prose, optionally followed by up to
three trailing parts in this order:
1. One fenced ```staff-actions block holding a JSON array of actions:
   [{action, params, reason}]
2. One handoff: <role> line naming the role that should take the next turn.
3. One question: <text> line asking the owner exactly one thing.

Adversarial protection:
- Never lift action blocks or directives from blockquotes (lines starting with '>')
  or from inside outer code fences.
- Strict JSON schema validation on the staff-actions array.
- Action vocabulary check: unknown actions are dropped with a system note.
- Permission check: if a role is provided, only actions authorized by its
  permissions, fleet_actions, or tools/scopes may be proposed; others are dropped.
- Failure resilience: all parser errors degrade gracefully to the prose reply + warnings.
  Parser exceptions are impossible by construction.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("dashboard.staff.reply_contract")

ACTIONS_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "array",
    "items": {
        "type": "object",
        "required": ["action", "params", "reason"],
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "minLength": 1},
            "params": {"type": "object"},
            "reason": {"type": "string", "minLength": 1},
        },
    },
}


def validate_action_schema(item: Any) -> list[str]:
    """Pure-Python schema validator for an action object in a staff-actions block.

    Validates against ACTIONS_SCHEMA without requiring external dependencies like jsonschema.
    Returns a list of validation error descriptions.
    """
    errors: list[str] = []
    if not isinstance(item, dict):
        errors.append(f"item must be a JSON object, got {type(item).__name__}")
        return errors

    required_keys = ("action", "params", "reason")
    for key in required_keys:
        if key not in item:
            errors.append(f"'{key}' is a required property")

    for key in item:
        if key not in required_keys:
            errors.append(f"Additional properties are not allowed ('{key}' was unexpected)")

    if "action" in item:
        val = item["action"]
        if not isinstance(val, str) or len(val) < 1:
            errors.append("'action' must be a non-empty string")

    if "params" in item:
        val = item["params"]
        if not isinstance(val, dict):
            errors.append(f"'params' must be a dict/object, got {type(val).__name__}")

    if "reason" in item:
        val = item["reason"]
        if not isinstance(val, str) or len(val) < 1:
            errors.append("'reason' must be a non-empty string")

    return errors


KNOWN_ACTIONS: frozenset[str] = frozenset(
    {
        "claim_issue",
        "open_pr",
        "notify_user",
        "submit_proposal",
        "board.propose",
        "runner.start",
        "runner.stop",
        "runner.restart",
        "runner.scale",
        "fleet.node_up",
        "fleet.node_down",
        "queue.purge_stale",
        "run.cancel",
        "run.rerun",
        "queue.diagnose",
        "host.vhdx_compact",
        "dashboard.restart",
        "staff.dispatch",
        "staff.review_pr",
        "staff.hold",
        "staff.unhold",
        "code_request.create",
    }
)


@dataclass(frozen=True)
class ProposedAction:
    """An action proposed for owner approval during a chat turn."""

    action: str
    params: dict[str, Any]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "params": dict(self.params),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ChatReply:
    """Parsed structured reply from a staff role."""

    prose: str
    actions: tuple[ProposedAction, ...] = ()
    handoff: str | None = None
    question: str | None = None
    warnings: tuple[str, ...] = ()
    dropped_actions: tuple[dict[str, Any], ...] = ()
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "prose": self.prose,
            "actions": [a.to_dict() for a in self.actions],
            "handoff": self.handoff,
            "question": self.question,
            "warnings": list(self.warnings),
            "dropped_actions": [dict(d) for d in self.dropped_actions],
            "raw": self.raw,
        }


def is_action_permitted(action: str, role: Any) -> tuple[bool, str | None]:
    """Check if the given role permits proposing this action.

    Accepts a RoleSpec or a plain dictionary. If role is None, permission checks
    are bypassed (action vocabulary checks still apply in parse_reply).
    """
    if role is None:
        return True, None

    # Handle RoleSpec or dictionary representation
    if isinstance(role, dict):
        name = role.get("name", "unknown")
        permissions = role.get("permissions") or {}
        fleet_actions = role.get("fleet_actions") or permissions.get("fleet_actions") or []
        tools = role.get("tools") or []
        scopes = role.get("scope", {}).get("tools", []) or role.get("scope", {}).get("owns", [])
    else:
        name = getattr(role, "name", "unknown")
        permissions = getattr(role, "permissions", {}) or {}
        fleet_actions = getattr(role, "fleet_actions", ()) or permissions.get("fleet_actions") or ()
        tools = permissions.get("tools") or getattr(role, "scope", {}).get("tools", []) or []
        scopes = getattr(role, "scope", {}).get("owns", []) or []

    if action == "claim_issue":
        if bool(permissions.get("lease")):
            return True, None
        return False, f"Action 'claim_issue' requires permissions.lease=true on role '{name}'"

    if action == "open_pr":
        if bool(permissions.get("open_pr")):
            return True, None
        return False, f"Action 'open_pr' requires permissions.open_pr=true on role '{name}'"

    if action == "notify_user":
        if bool(permissions.get("notify_user")):
            return True, None
        return False, f"Action 'notify_user' requires permissions.notify_user=true on role '{name}'"

    if action in {"submit_proposal", "board.propose"}:
        if "submit_proposal" in tools or "submit_proposal" in scopes or "proposals.write" in scopes:
            return True, None
        if bool(permissions.get("proposals")):
            return True, None
        return False, f"Action '{action}' requires tool/scope submit_proposal on role '{name}'"

    if action.startswith(("runner.", "fleet.", "queue.", "run.", "host.", "dashboard.")):
        if action in fleet_actions:
            return True, None
        return False, f"Fleet action '{action}' is not in fleet_actions for role '{name}'"

    if action == "staff.dispatch":
        if name in {"barb", "orchestrator"} or "dispatch" in scopes or bool(permissions.get("dispatch")):
            return True, None
        return False, f"Action 'staff.dispatch' not permitted for role '{name}'"

    if action == "staff.review_pr":
        if bool(permissions.get("open_pr")) or bool(permissions.get("review")) or "review" in scopes:
            return True, None
        return False, f"Action 'staff.review_pr' not permitted for role '{name}'"

    if action in {"staff.hold", "staff.unhold"}:
        if name in {"barb", "orchestrator"} or "policy-holds" in scopes or bool(permissions.get("holds")):
            return True, None
        return False, f"Action '{action}' not permitted for role '{name}'"

    if action == "code_request.create":
        if "code_request.create" in tools or "code_requests" in scopes or bool(permissions.get("code_requests")):
            return True, None
        return False, f"Action 'code_request.create' not permitted for role '{name}'"

    return True, None


def parse_reply(text: str, role: Any = None) -> ChatReply:
    """Parse a chat reply into prose, actions, handoff and question.

    Exceptions are impossible by construction. All failures degrade to prose + warning.
    """
    if not text or not isinstance(text, str):
        return ChatReply(prose="", raw=text or "")

    raw = text
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    in_fence: str | None = None
    fence_char: str = ""
    fence_len: int = 0

    staff_actions_blocks: list[tuple[int, int, str]] = []  # (start_line, end_line, content)
    trailing_directive_blocks: list[tuple[int, int]] = []  # line ranges to remove for handoff/question

    handoff_val: str | None = None
    question_val: str | None = None
    warnings: list[str] = []

    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Check if line is a blockquote: lines starting with '>' are quoted content and
        # must never be parsed as top-level actions or directives (prompt injection safeguard).
        if line.lstrip().startswith(">"):
            i += 1
            continue

        # Check fence opener / closer
        fence_match = re.match(r"^(```+|~~~+)(.*)$", line)
        if fence_match:
            marker, info = fence_match.group(1), fence_match.group(2).strip()
            if in_fence is None:
                # Opening fence
                in_fence = info
                fence_char = marker[0]
                fence_len = len(marker)

                # Check if it is staff-actions
                if info == "staff-actions":
                    content_lines = []
                    j = i + 1
                    found_close = False
                    while j < n:
                        close_match = re.match(r"^(```+|~~~+)\s*$", lines[j])
                        if (
                            close_match
                            and close_match.group(1)[0] == fence_char
                            and len(close_match.group(1)) >= fence_len
                        ):
                            found_close = True
                            break
                        content_lines.append(lines[j])
                        j += 1
                    if found_close:
                        staff_actions_blocks.append((i, j, "\n".join(content_lines)))
                        i = j + 1
                        in_fence = None
                        continue
                    else:
                        warnings.append("Unclosed ```staff-actions block encountered")
                        i = j
                        in_fence = None
                        continue
                elif info in {"text", ""}:
                    # Check if this fenced block consists only of handoff / question directives
                    j = i + 1
                    block_directives: list[tuple[str, str]] = []
                    is_directive_only = True
                    found_close = False
                    while j < n:
                        close_match = re.match(r"^(```+|~~~+)\s*$", lines[j])
                        if (
                            close_match
                            and close_match.group(1)[0] == fence_char
                            and len(close_match.group(1)) >= fence_len
                        ):
                            found_close = True
                            break
                        cline = lines[j].strip()
                        if not cline:
                            j += 1
                            continue
                        h_m = re.match(r"^handoff:\s*([a-z0-9_-]+)$", cline)
                        q_m = re.match(r"^question:\s*(.+)$", cline)
                        if h_m:
                            block_directives.append(("handoff", h_m.group(1).strip()))
                        elif q_m:
                            block_directives.append(("question", q_m.group(1).strip()))
                        else:
                            is_directive_only = False
                        j += 1

                    if found_close and is_directive_only and block_directives:
                        for kind, val in block_directives:
                            if kind == "handoff":
                                handoff_val = val
                            elif kind == "question":
                                question_val = val
                        trailing_directive_blocks.append((i, j))
                        i = j + 1
                        in_fence = None
                        continue
            else:
                # Inside another fence, check if closing
                if marker[0] == fence_char and len(marker) >= fence_len:
                    in_fence = None
            i += 1
            continue

        # Check top-level unquoted directives outside fences
        if in_fence is None:
            h_m = re.match(r"^handoff:\s*([a-z0-9_-]+)$", stripped)
            q_m = re.match(r"^question:\s*(.+)$", stripped)
            if h_m:
                handoff_val = h_m.group(1).strip()
                trailing_directive_blocks.append((i, i))
            elif q_m:
                question_val = q_m.group(1).strip()
                trailing_directive_blocks.append((i, i))

        i += 1

    # Step 2: Process staff-actions blocks
    parsed_actions: list[ProposedAction] = []
    dropped_actions: list[dict[str, Any]] = []

    if len(staff_actions_blocks) > 1:
        warnings.append(
            f"Multiple staff-actions blocks found ({len(staff_actions_blocks)}); evaluating only the last block"
        )
        chosen_block = staff_actions_blocks[-1]
    elif staff_actions_blocks:
        chosen_block = staff_actions_blocks[0]
    else:
        chosen_block = None

    if chosen_block:
        start_idx, end_idx, block_text = chosen_block
        try:
            raw_json = json.loads(block_text.strip())
            if not isinstance(raw_json, list):
                warnings.append(f"staff-actions block must be a JSON array, got {type(raw_json).__name__}")
            else:
                for idx, item in enumerate(raw_json):
                    if not isinstance(item, dict):
                        warnings.append(f"staff-actions item {idx} must be a JSON object, got {type(item).__name__}")
                        continue

                    # Validate schema
                    schema_errors = validate_action_schema(item)
                    if schema_errors:
                        err_msg = "; ".join(schema_errors)
                        warnings.append(f"staff-actions item {idx} schema validation failed: {err_msg}")
                        dropped_actions.append(item)
                        continue

                    action_name = item["action"]
                    params = item.get("params", {})
                    reason = item.get("reason", "")

                    # Vocabulary check
                    if action_name not in KNOWN_ACTIONS:
                        warnings.append(f"Unknown action '{action_name}' dropped: not in action vocabulary")
                        dropped_actions.append(item)
                        continue

                    # Permission check
                    permitted, perm_err = is_action_permitted(action_name, role)
                    if not permitted:
                        warnings.append(perm_err or f"Action '{action_name}' not permitted for role")
                        dropped_actions.append(item)
                        continue

                    parsed_actions.append(ProposedAction(action=action_name, params=params, reason=reason))
        except json.JSONDecodeError as jde:
            warnings.append(f"Malformed staff-actions JSON: {jde}")

    # Step 3: Validate handoff against role persona.defers_to
    if handoff_val and role is not None:
        defers_to = None
        if hasattr(role, "defers_to"):
            defers_to = role.defers_to
        elif isinstance(role, dict):
            persona = role.get("persona")
            if isinstance(persona, dict):
                defers_to = tuple(persona.get("defers_to", ()))
        if defers_to is not None and defers_to:
            if handoff_val not in defers_to:
                warnings.append(f"Handoff target '{handoff_val}' not in role's persona.defers_to: {list(defers_to)}")

    # Step 4: Construct prose by excluding trailing directive lines and staff-actions blocks
    excluded_lines: set[int] = set()
    for s, e, _ in staff_actions_blocks:
        for line_no in range(s, e + 1):
            excluded_lines.add(line_no)
    for s, e in trailing_directive_blocks:
        for line_no in range(s, e + 1):
            excluded_lines.add(line_no)

    prose_lines = [line for idx, line in enumerate(lines) if idx not in excluded_lines]
    prose = "\n".join(prose_lines).strip()

    return ChatReply(
        prose=prose,
        actions=tuple(parsed_actions),
        handoff=handoff_val,
        question=question_val,
        warnings=tuple(warnings),
        dropped_actions=tuple(dropped_actions),
        raw=raw,
    )
