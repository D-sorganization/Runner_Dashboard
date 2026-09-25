"""Pure-Python role definition schema validation (#1300).

Mirrors ``staff/schema.json`` and ``Repository_Management/shared_scripts/staff_roles.py``
without requiring external dependencies like ``jsonschema``.
"""

from __future__ import annotations

import re
from typing import Any, TypeGuard

from staff.adapters import CHAT_READ_ONLY_TOOLS

SURFACES: tuple[str, ...] = ("grok-chat", "dashboard", "both")
REQUIRED_FIELDS: tuple[str, ...] = (
    "name",
    "title",
    "summary",
    "playbook",
    "prompt_template",
    "instructions",
    "providers",
    "model",
    "schedule",
    "window",
    "repos",
    "scope",
    "budget",
    "permissions",
    "reports_to",
    "holds",
    "surface",
)
OPTIONAL_FIELDS: tuple[str, ...] = (
    "strategy",
    "retired",
    "retired_reason",
    "persona",
    "chat",
    "group",
    "tools",
    "scopes",
)
STRATEGY_KEYS: tuple[str, ...] = ("consolidate_when",)
CONSOLIDATE_WHEN_BOUNDS: dict[str, tuple[int, int | None]] = {
    "open_prs": (1, None),
    "utilisation_pct": (1, 100),
}
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
APPROVAL_LEVELS: tuple[str, ...] = ("auto", "confirm", "owner")
_APPROVAL_ORDER: dict[str, int] = {"auto": 0, "confirm": 1, "owner": 2}
DEFAULT_ACTION_APPROVALS: dict[str, str] = {
    "dashboard.restart": "confirm",
    "fleet.node_down": "owner",
    "fleet.node_up": "confirm",
    "host.vhdx_compact": "confirm",
    "queue.diagnose": "auto",
    "queue.purge_stale": "auto",
    "run.cancel": "confirm",
    "run.rerun": "auto",
    "runner.restart": "confirm",
    "runner.scale": "confirm",
    "runner.start": "confirm",
    "runner.stop": "confirm",
}
REQUIRED_PERMISSION_KEYS: tuple[str, ...] = (
    "lease",
    "push_branch",
    "open_pr",
    "merge",
    "host_shell",
    "notify_user",
)
OPTIONAL_PERMISSION_KEYS: tuple[str, ...] = (
    "fleet_actions",
    "approvals",
    "action_approvals",
)
PERMISSION_KEYS: tuple[str, ...] = REQUIRED_PERMISSION_KEYS
BUDGET_KEYS: tuple[str, ...] = ("usd_per_run", "usd_per_day")

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PLAYBOOK_RE = re.compile(r"^docs/.+\.md$")
_TEMPLATE_RE = re.compile(r"^docs/templates/.+\.md$")
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_CRON_FIELD_RE = re.compile(r"^[\d*,/-]+$")
_CRON_RANGES: tuple[tuple[int, int], ...] = (
    (0, 59),
    (0, 23),
    (1, 31),
    (1, 12),
    (0, 7),
)


def _is_number(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_cron(expr: str) -> list[str]:
    """Return the five cron fields of ``expr`` or raise ``ValueError``."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"expected 5 cron fields, got {len(fields)}: {expr!r}")
    for field, (low, high) in zip(fields, _CRON_RANGES, strict=True):
        if not _CRON_FIELD_RE.match(field):
            raise ValueError(f"bad cron field {field!r} in {expr!r}")
        for part in field.split(","):
            base, _, step = part.partition("/")
            if step and (not step.isdigit() or int(step) == 0):
                raise ValueError(f"bad cron step {part!r} in {expr!r}")
            if base == "*":
                continue
            bounds = base.split("-")
            if len(bounds) > 2 or not all(b.isdigit() for b in bounds):
                raise ValueError(f"bad cron value {part!r} in {expr!r}")
            for bound in bounds:
                if not low <= int(bound) <= high:
                    raise ValueError(f"cron value {bound} out of range {low}-{high} in {expr!r}")
    return fields


def _validate_strings(data: dict[str, Any]) -> list[str]:
    problems: list[str] = []

    def expect_str(key: str, *, allow_none: bool = False) -> str | None:
        value = data.get(key)
        if value is None and allow_none:
            return None
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{key} must be a non-empty string")
            return None
        return value

    name = expect_str("name")
    if name is not None and not _NAME_RE.match(name):
        problems.append(f"name {name!r} must match {_NAME_RE.pattern}")
    expect_str("title")
    summary = expect_str("summary")
    if summary is not None and len(summary) > 300:
        problems.append("summary must be at most 300 characters")
    playbook = expect_str("playbook")
    if playbook is not None and not _PLAYBOOK_RE.match(playbook):
        problems.append(f"playbook {playbook!r} must be a docs/*.md path")
    template = expect_str("prompt_template", allow_none=True)
    if template is not None and not _TEMPLATE_RE.match(template):
        problems.append(f"prompt_template {template!r} must be a docs/templates/*.md path")
    expect_str("instructions")
    expect_str("model")
    reports_to = expect_str("reports_to")
    if reports_to is not None and not (_NAME_RE.match(reports_to) or reports_to == "user"):
        problems.append(f"reports_to {reports_to!r} must be a role name or 'user'")
    return problems


def _string_list_problems(key: str, value: Any) -> list[str]:
    """Optional grant lists (``tools``, ``scopes``, RM#1732): unique non-empty strings.

    The allowed vocabulary is enforced by the RM validator, the source of truth.
    """
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        return [f"{key} must be a list of non-empty strings"]
    if len(set(value)) != len(value):
        return [f"{key} must be unique"]
    return []


def _validate_collections(data: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    providers = data.get("providers")
    if not isinstance(providers, list) or not providers:
        problems.append("providers must be a non-empty list")
    else:
        if any(not isinstance(p, str) or not p.strip() for p in providers):
            problems.append("providers must contain non-empty strings")
        elif len(set(providers)) != len(providers):
            problems.append("providers must be unique")

    repos = data.get("repos")
    if not isinstance(repos, list) or not repos:
        problems.append("repos must be a non-empty list")
    else:
        bad = [r for r in repos if not isinstance(r, str) or not _REPO_RE.match(r)]
        if bad:
            problems.append(f"bad repo names {bad!r}")
        if len(set(repos)) != len(repos):
            problems.append("repos must be unique")

    for key in ("tools", "scopes"):
        if key in data:
            problems.extend(_string_list_problems(key, data[key]))

    holds = data.get("holds")
    if not isinstance(holds, list) or any(not isinstance(h, str) or not h.strip() for h in holds):
        problems.append("holds must be a list of non-empty strings")
    return problems


def _validate_dicts(data: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    schedule = data.get("schedule")
    if schedule is not None:
        if not isinstance(schedule, str):
            problems.append("schedule must be a cron string or null")
        else:
            try:
                parse_cron(schedule)
            except ValueError as exc:
                problems.append(f"schedule: {exc}")

    window = data.get("window")
    if window is not None:
        if not isinstance(window, dict) or set(window) != {"start", "end"}:
            problems.append("window must be null or {start, end}")
        else:
            for key in ("start", "end"):
                val = window.get(key)
                if not isinstance(val, str) or not _HHMM_RE.match(val):
                    problems.append(f"window.{key} must be HH:MM")

    if not isinstance(data.get("scope"), dict):
        problems.append("scope must be a mapping")

    budget = data.get("budget")
    if not isinstance(budget, dict) or not set(BUDGET_KEYS).issubset(set(budget)):
        problems.append(f"budget must contain {list(BUDGET_KEYS)}")
    else:
        for key in BUDGET_KEYS:
            val = budget.get(key)
            if not _is_number(val) or val < 0:
                problems.append(f"budget.{key} must be a number >= 0")

    permissions = data.get("permissions")
    if not isinstance(permissions, dict) or not set(PERMISSION_KEYS).issubset(set(permissions)):
        problems.append(f"permissions must contain {list(PERMISSION_KEYS)}")
    else:
        for key in PERMISSION_KEYS:
            if not isinstance(permissions.get(key), bool):
                problems.append(f"permissions.{key} must be a boolean")
        for key in permissions:
            if key not in REQUIRED_PERMISSION_KEYS and key not in OPTIONAL_PERMISSION_KEYS:
                problems.append(f"unknown permissions key {key!r}")
        problems.extend(_validate_fleet_actions_and_approvals(permissions))
    return problems


def _validate_fleet_actions_and_approvals(permissions: dict[str, Any]) -> list[str]:
    """Check permissions.fleet_actions and approvals (#1734, SC-E1, SC-E2)."""
    problems: list[str] = []
    fleet_actions = permissions.get("fleet_actions")
    if fleet_actions is not None:
        if not isinstance(fleet_actions, list):
            problems.append("fleet_actions must be a list")
        else:
            unknown = [a for a in fleet_actions if a not in FLEET_ACTIONS]
            if unknown:
                problems.append(f"unknown fleet action {unknown!r}")
            if len(set(map(repr, fleet_actions))) != len(fleet_actions):
                problems.append("fleet_actions must be unique")

    approvals = permissions.get("approvals")
    if approvals is None:
        approvals = permissions.get("action_approvals")
    if approvals is not None:
        if not isinstance(approvals, dict):
            problems.append("approvals must be a mapping")
        elif fleet_actions is None:
            problems.append("approvals declared without fleet_actions")
        else:
            granted_actions = set(fleet_actions) if isinstance(fleet_actions, list) else set()
            for action, level in approvals.items():
                if action not in FLEET_ACTIONS:
                    problems.append(f"unknown fleet action in approvals: {action!r}")
                elif action not in granted_actions:
                    problems.append(f"approval for {action!r} requires action in fleet_actions")
                if level not in APPROVAL_LEVELS:
                    problems.append(f"unknown approval level {level!r} for {action!r}")
                elif action in DEFAULT_ACTION_APPROVALS:
                    default = DEFAULT_ACTION_APPROVALS[action]
                    if _APPROVAL_ORDER.get(level, 0) < _APPROVAL_ORDER[default]:
                        problems.append(
                            f"approval for {action!r} cannot loosen default policy {default!r} to {level!r}"
                        )
    return problems


def _validate_lifecycle(data: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    surface = data.get("surface")
    if surface not in SURFACES:
        problems.append(f"surface must be one of {SURFACES}")
    elif surface == "grok-chat":
        if data.get("providers") != ["grok-chat"]:
            problems.append("grok-chat surface must use providers [grok-chat]")
        permissions = data.get("permissions")
        if isinstance(permissions, dict) and permissions.get("host_shell"):
            problems.append("grok-chat surface must not have host_shell")

    if "strategy" in data:
        strategy = data["strategy"]
        if not isinstance(strategy, dict):
            problems.append("strategy must be a mapping")
        else:
            for key in strategy:
                if key not in STRATEGY_KEYS:
                    problems.append(f"unknown strategy key {key!r}")
            if "consolidate_when" in strategy:
                when = strategy["consolidate_when"]
                if not isinstance(when, dict) or not when:
                    problems.append("strategy.consolidate_when must be a non-empty mapping")
                else:
                    for k, v in when.items():
                        if k not in CONSOLIDATE_WHEN_BOUNDS:
                            problems.append(f"unknown strategy.consolidate_when key {k!r}")
                            continue
                        low, high = CONSOLIDATE_WHEN_BOUNDS[k]
                        invalid = not isinstance(v, int) or isinstance(v, bool) or v < low
                        if high is not None and isinstance(v, int) and v > high:
                            invalid = True
                        if invalid:
                            lim = f"{low}..{high}" if high is not None else f">= {low}"
                            problems.append(f"strategy.consolidate_when.{k} must be an integer {lim}")

    retired = data.get("retired", False)
    if not isinstance(retired, bool):
        problems.append("retired must be a boolean")
    elif retired:
        reason = data.get("retired_reason")
        if not isinstance(reason, str) or not reason.strip():
            problems.append("retired roles must give retired_reason")
    elif "retired_reason" in data and not isinstance(data["retired_reason"], str):
        problems.append("retired_reason must be a string")
    return problems


def _validate_chat(data: dict[str, Any]) -> list[str]:
    """``chat.read_only_tools`` must use the read-only vocabulary (#1484)."""
    chat = data.get("chat")
    if not isinstance(chat, dict) or "read_only_tools" not in chat:
        return []
    tools = chat["read_only_tools"]
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        return ["chat.read_only_tools must be a list of strings"]
    unknown = [t for t in tools if t not in CHAT_READ_ONLY_TOOLS]
    if unknown:
        return [f"chat.read_only_tools has non-read-only tools {unknown}; known: {sorted(CHAT_READ_ONLY_TOOLS)}"]
    return []


def validate_role_data(data: Any) -> list[str]:
    """Validate parsed role mapping against staff/schema.json."""
    if not isinstance(data, dict):
        return ["top level is not a mapping"]

    problems: list[str] = []
    allowed = set(REQUIRED_FIELDS) | set(OPTIONAL_FIELDS)
    for key in REQUIRED_FIELDS:
        if key not in data:
            problems.append(f"missing required field {key!r}")
    for key in data:
        if key not in allowed:
            problems.append(f"unknown field {key!r}")
    if problems:
        return problems

    problems.extend(_validate_strings(data))
    problems.extend(_validate_collections(data))
    problems.extend(_validate_dicts(data))
    problems.extend(_validate_lifecycle(data))
    problems.extend(_validate_chat(data))
    return problems
