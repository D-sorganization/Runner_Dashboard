"""Playbook assignment: parameter resolution, lane eligibility, fail-closed create (#3488)."""

from __future__ import annotations

import threading
from typing import Any

from staff.playbooks.schema import PlaybookAssignmentState, PlaybookDefinition, PlaybookValidationError

_LOCK = threading.Lock()
_ASSIGNMENTS: list[dict[str, Any]] = []
_NEXT_ID = 0


class PlaybookUnauthorizedError(PermissionError):
    """Raised when the requesting role is not in the playbook's ``role_refs``."""


def clear_assignments() -> None:
    """Reset the in-memory assignment log (used by tests)."""
    global _NEXT_ID
    with _LOCK:
        _ASSIGNMENTS.clear()
        _NEXT_ID = 0


def list_assignments() -> list[dict[str, Any]]:
    with _LOCK:
        return [dict(a) for a in _ASSIGNMENTS]


def resolve_parameters(playbook: PlaybookDefinition, requested: dict[str, Any]) -> dict[str, Any]:
    """Apply defaults and validate requested parameters; raises on any violation."""
    resolved: dict[str, Any] = {}
    for param in playbook.parameters:
        value = requested.get(param.name, param.default)
        if value is None:
            if param.required and param.default is None:
                raise PlaybookValidationError(f"missing required parameter {param.name!r}")
            resolved[param.name] = param.default
            continue
        if param.param_type == "enum" and value not in param.choices:
            raise PlaybookValidationError(f"parameter {param.name!r} must be one of {param.choices!r}, got {value!r}")
        if param.param_type == "integer" and not isinstance(value, int):
            raise PlaybookValidationError(f"parameter {param.name!r} must be an integer")
        if param.param_type == "boolean" and not isinstance(value, bool):
            raise PlaybookValidationError(f"parameter {param.name!r} must be a boolean")
        if param.param_type == "string" and not isinstance(value, str):
            raise PlaybookValidationError(f"parameter {param.name!r} must be a string")
        resolved[param.name] = value
    unknown = set(requested) - {p.name for p in playbook.parameters}
    if unknown:
        raise PlaybookValidationError(f"unknown parameter(s): {sorted(unknown)!r}")
    return resolved


def create_assignment(playbook: PlaybookDefinition, role: str, requested_parameters: dict[str, Any]) -> dict[str, Any]:
    """Validate eligibility and parameters, then record a new ``ASSIGNED`` assignment.

    Fails closed: raises without writing anything when the role is ineligible
    (``PlaybookUnauthorizedError``) or parameters are invalid (``PlaybookValidationError``).
    """
    if role not in playbook.role_refs:
        raise PlaybookUnauthorizedError(f"role {role!r} is not eligible for playbook {playbook.playbook_id!r}")
    resolved = resolve_parameters(playbook, requested_parameters)

    global _NEXT_ID
    with _LOCK:
        _NEXT_ID += 1
        record = {
            "assignment_id": _NEXT_ID,
            "playbook_id": playbook.playbook_id,
            "pin": playbook.pin,
            "role": role,
            "parameters": resolved,
            "state": PlaybookAssignmentState.ASSIGNED.value,
        }
        _ASSIGNMENTS.append(record)
        return dict(record)
