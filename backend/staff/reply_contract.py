"""Reply contract parser for staff chat turns (SC-B5, #1308).

Replaces the flat ``STAFF_RESULT:`` convention for conversational turns with a
structured contract:
1. Markdown prose reply.
2. Optional trailing fenced ```staff-actions JSON block:
   [{"action": "...", "params": {...}, "reason": "..."}]
3. Optional trailing ``handoff: <role>`` line or ```text block.
4. Optional trailing ``question: <text>`` line or ```text block.

Guarantees:
- Parser exceptions are impossible by construction (fail-safe to prose + warning).
- Malformed action blocks never discard or lose the prose reply.
- Actions are validated against the organization action registry and the
  role's declared permissions (SC-E1 schema). Unknown or unauthorized
  actions are dropped with recorded warnings.
- Adversarial defense: action blocks inside Markdown blockquotes (> ...)
  or nested within outer code blocks are treated as quoted data, not
  action proposals.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator, Set
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from staff.roles import RoleSpec

if TYPE_CHECKING:
    from staff.actions import ActionRegistry

logger = logging.getLogger(__name__)

# Standard action permissions requirements (retained for backward compatibility)
STANDARD_ACTION_REQUIREMENTS: dict[str, tuple[str, str]] = {
    "claim_issue": ("permissions", "lease"),
    "open_pr": ("permissions", "open_pr"),
    "notify_user": ("permissions", "notify_user"),
    "submit_proposal": ("tools", "submit_proposal"),
}

# 12 Fleet maintenance actions from SC-E1 (Repository_Management#1734)
FLEET_ACTIONS: tuple[str, ...] = (
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
)


def get_known_actions(registry: ActionRegistry | None = None) -> frozenset[str]:
    """Return the set of all action names registered in the action registry (SC-B1-G3, #1486)."""
    from staff.actions import ACTION_REGISTRY

    reg = registry or ACTION_REGISTRY
    return frozenset(a.name for a in reg.list_actions())


class _DynamicKnownActions(Set[str]):
    """Dynamic Set view that mirrors the action registry."""

    def __iter__(self) -> Iterator[str]:
        return iter(get_known_actions())

    def __contains__(self, item: object) -> bool:
        return item in get_known_actions()

    def __len__(self) -> int:
        return len(get_known_actions())

    def __repr__(self) -> str:
        return repr(get_known_actions())

    def __eq__(self, other: object) -> bool:
        return get_known_actions() == other


ALL_KNOWN_ACTIONS: _DynamicKnownActions = _DynamicKnownActions()

_HANDOFF_RE = re.compile(r"^\s*handoff:\s*([a-zA-Z0-9_-]+)\s*$", re.IGNORECASE)
_QUESTION_RE = re.compile(r"^\s*question:\s*(.+)$", re.IGNORECASE)


def generate_chat_contract_text(registry: ActionRegistry | None = None) -> str:
    """Generate the chat reply contract prompt dynamically from the action registry (DRY)."""
    from staff.actions import ACTION_REGISTRY

    reg = registry or ACTION_REGISTRY
    actions = reg.list_actions()

    rows = []
    for a in actions:
        desc = a.description.replace("|", "\\|").strip()
        rows.append(f"| `{a.name}` | {a.risk_class} | `{a.required_scope}` | {desc} |")
    action_table = "\n".join(rows)

    return f"""# Chat Reply Contract (Shared Fragment)

A reply is **plain markdown prose**, optionally followed by up to three trailing parts:
1. One fenced ```staff-actions block holding a JSON array: [{{"action": "...", "params": {{...}}, "reason": "..."}}]
2. One handoff: <role> line naming the role for the next turn.
3. One question: <text> line asking the owner one thing.

The prose is the answer. The trailing parts are optional and never a substitute for answering.

## The `staff-actions` Block

```staff-actions
[
  {{
    "action": "staff.dispatch",
    "params": {{"role": "planner", "prompt": "Analyze issue #1486"}},
    "reason": "One sentence: why this, why now."
  }}
]
```

- **Propose, never perform.** An action in the block is a request for owner approval.
- **One reason per action**, concrete and checkable.
- **Strict JSON.** No comments, no trailing commas, no single quotes.
- Actions are validated against the organization action registry and the role's declared permissions.

### Actions You May Propose

| Action | Risk | Required Scope | Description |
| ------ | ---- | -------------- | ----------- |
{action_table}

## `handoff:`

handoff: <role>
Name one role from your `persona.defers_to` when the next step belongs to another role's lane.

## `question:`

question: <text>
Ask the owner one question when you cannot proceed without an owner decision.
""".strip()


def get_chat_contract_text(
    rel: str = "staff/prompts/_chat_contract.md",
    registry: ActionRegistry | None = None,
) -> str:
    """Retrieve the chat reply contract fragment generated dynamically from the action registry."""
    return generate_chat_contract_text(registry=registry)


@dataclass(frozen=True)
class ProposedAction:
    """A proposed action inside a chat reply's staff-actions block."""

    action: str
    params: dict[str, Any]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "params": dict(self.params),
            "reason": self.reason,
        }


@dataclass
class ParsedReply:
    """The structured result of parsing a conversational turn reply."""

    reply: str
    actions: list[ProposedAction] = field(default_factory=list)
    handoff: str | None = None
    question: str | None = None
    warnings: list[str] = field(default_factory=list)
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "reply": self.reply,
            "actions": [a.to_dict() for a in self.actions],
            "handoff": self.handoff,
            "question": self.question,
            "warnings": list(self.warnings),
        }


def is_action_allowed_for_role(
    action: str,
    role: RoleSpec | dict[str, Any],
    registry: ActionRegistry | None = None,
) -> bool:
    """Check whether a role is authorized to propose ``action`` using registry policy (SC-B1-G3)."""
    from staff.actions import ACTION_REGISTRY, check_role_permission
    from staff.roles import RoleSpec, parse_role

    reg = registry or ACTION_REGISTRY
    action_def = reg.get(action)
    if not action_def:
        return False

    if isinstance(role, RoleSpec):
        return check_role_permission(action_def, role.name, role_spec=role)
    elif isinstance(role, dict):
        role_name = str(role.get("name") or "")
        try:
            role_spec = parse_role(role)
        except Exception:
            role_spec = None
        return check_role_permission(action_def, role_name, role_spec=role_spec)

    return False


def _is_blockquote_line(line: str) -> bool:
    return line.lstrip().startswith(">")


def _is_fence_line(line: str) -> tuple[bool, str]:
    """Check if line is a markdown code fence. Returns (is_fence, tag)."""
    trimmed = line.strip()
    if trimmed.startswith("```"):
        tag = trimmed.lstrip("`").strip().lower()
        return True, tag
    if trimmed.startswith("~~~"):
        tag = trimmed.lstrip("~").strip().lower()
        return True, tag
    return False, ""


def parse_reply(
    raw_text: str | None,
    role: RoleSpec | dict[str, Any] | None = None,
    registry: ActionRegistry | None = None,
) -> ParsedReply:
    """Parse a conversational turn reply into prose, actions, handoff, and question.

    Exceptions are impossible by construction: all malformed inputs fail
    safely by keeping the prose reply intact and adding diagnostic warnings.
    """
    if not raw_text:
        return ParsedReply(reply="", raw=raw_text or "")

    raw = raw_text
    warnings: list[str] = []
    actions: list[ProposedAction] = []
    handoff: str | None = None
    question: str | None = None

    lines = raw.splitlines()

    # Step 1: Scan lines identifying top-level fences vs blockquotes / nested code blocks
    in_code_block = False

    # Line classification: 'prose', 'staff_actions_header', 'staff_actions_body',
    # 'staff_actions_footer', 'handoff_or_question'
    line_kinds: list[str] = ["prose"] * len(lines)
    staff_actions_blocks: list[tuple[int, int, str]] = []  # (start_line, end_line, raw_json)

    current_actions_start = -1
    current_actions_lines: list[str] = []

    for i, line in enumerate(lines):
        if _is_blockquote_line(line):
            # Blockquotes are never top-level actionable blocks (adversarial defense)
            continue

        is_fence, tag = _is_fence_line(line)
        if is_fence:
            if not in_code_block:
                in_code_block = True
                if tag == "staff-actions":
                    current_actions_start = i
                    current_actions_lines = []
                    line_kinds[i] = "staff_actions_header"
            else:
                # Closing fence
                in_code_block = False
                if current_actions_start != -1:
                    line_kinds[i] = "staff_actions_footer"
                    staff_actions_blocks.append((current_actions_start, i, "\n".join(current_actions_lines)))
                    current_actions_start = -1
                    current_actions_lines = []
        else:
            if current_actions_start != -1:
                line_kinds[i] = "staff_actions_body"
                current_actions_lines.append(line)

    # Step 2: Parse staff-actions JSON block(s)
    if staff_actions_blocks:
        if len(staff_actions_blocks) > 1:
            warnings.append(
                f"Multiple ({len(staff_actions_blocks)}) staff-actions blocks found; only the first was parsed."
            )
        _, _, raw_json = staff_actions_blocks[0]
        try:
            data = json.loads(raw_json)
            if not isinstance(data, list):
                warnings.append("staff-actions block must be a JSON array")
            else:
                for idx, item in enumerate(data):
                    if not isinstance(item, dict):
                        warnings.append(f"Action item at index {idx} must be an object")
                        continue
                    action_name = item.get("action")
                    if not isinstance(action_name, str) or not action_name.strip():
                        warnings.append(f"Action item at index {idx} missing 'action' name")
                        continue
                    action_name = action_name.strip()
                    reason = item.get("reason")
                    if not isinstance(reason, str) or not reason.strip():
                        warnings.append(f"Action '{action_name}' missing reason")
                        continue
                    reason = reason.strip()
                    params = item.get("params")
                    if params is None:
                        params = {}
                    elif not isinstance(params, dict):
                        warnings.append(f"Action '{action_name}' params must be an object")
                        continue

                    # Vocabulary check (SC-B1-G3: single action registry vocabulary)
                    known_actions = get_known_actions(registry)
                    if action_name not in known_actions:
                        warnings.append(f"Action '{action_name}' is unknown and was dropped.")
                        continue

                    # Authorization check against role
                    if role is not None and not is_action_allowed_for_role(action_name, role, registry=registry):
                        role_name = getattr(
                            role,
                            "name",
                            role.get("name", "unknown") if isinstance(role, dict) else "unknown",
                        )
                        warnings.append(
                            f"Role '{role_name}' does not hold permission for action '{action_name}'; action dropped."
                        )
                        continue

                    actions.append(ProposedAction(action=action_name, params=params, reason=reason))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Malformed JSON in staff-actions block: {exc}")

    # Step 3: Scan for handoff and question (inside ```text fence or as top-level trailing lines)
    # Check lines outside staff-actions blocks
    in_text_fence = False
    text_fence_lines: list[int] = []

    for i, line in enumerate(lines):
        if line_kinds[i] != "prose":
            continue
        if _is_blockquote_line(line):
            continue

        is_fence, tag = _is_fence_line(line)
        if is_fence:
            if not in_text_fence:
                if tag in ("text", ""):
                    in_text_fence = True
                    text_fence_lines = [i]
            else:
                in_text_fence = False
                text_fence_lines.append(i)
                # If text fence only contains handoff/question, mark all text fence lines
                text_content = [
                    lines[k]
                    for k in text_fence_lines[1:-1]
                    if not _HANDOFF_RE.match(lines[k]) and not _QUESTION_RE.match(lines[k]) and lines[k].strip()
                ]
                if not text_content:
                    for k in text_fence_lines:
                        line_kinds[k] = "handoff_or_question"
            continue

        m_handoff = _HANDOFF_RE.match(line)
        if m_handoff:
            handoff = m_handoff.group(1).strip()
            line_kinds[i] = "handoff_or_question"
            continue

        m_question = _QUESTION_RE.match(line)
        if m_question:
            question = m_question.group(1).strip()
            line_kinds[i] = "handoff_or_question"
            continue

    # Step 4: Validate handoff against role's defers_to
    if handoff and role is not None:
        defers_to: tuple[str, ...] = ()
        if isinstance(role, RoleSpec):
            defers_to = role.defers_to
        elif isinstance(role, dict):
            persona_data = role.get("persona")
            if isinstance(persona_data, dict):
                defers_to = tuple(str(x) for x in persona_data.get("defers_to", ()))
        if defers_to and handoff not in defers_to:
            role_name = getattr(role, "name", role.get("name", "unknown") if isinstance(role, dict) else "unknown")
            warnings.append(
                f"Role '{role_name}' handed off to '{handoff}', which is not in persona.defers_to ({list(defers_to)})."
            )

    # Step 5: Assemble cleaned prose reply
    prose_lines = [lines[i] for i in range(len(lines)) if line_kinds[i] == "prose"]
    reply = "\n".join(prose_lines).strip()

    return ParsedReply(
        reply=reply,
        actions=actions,
        handoff=handoff,
        question=question,
        warnings=warnings,
        raw=raw,
    )
