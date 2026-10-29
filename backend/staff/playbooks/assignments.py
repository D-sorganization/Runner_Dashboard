"""Playbook assignment: parameter resolution, lane eligibility, fail-closed create (#3488);
lane fetch/ack/completion lifecycle, version-hash stamping, and the O(N) swarm sweep (#3489)."""

from __future__ import annotations

import threading
import time
from typing import Any

from staff.playbooks.schema import (
    PlaybookAssignmentState,
    PlaybookDefinition,
    PlaybookValidationError,
    validate_assignment_transition,
)

_LOCK = threading.Lock()
_ASSIGNMENTS: list[dict[str, Any]] = []
_NEXT_ID = 0

_S = PlaybookAssignmentState


class PlaybookUnauthorizedError(PermissionError):
    """Raised when the requesting role is not in the playbook's ``role_refs``."""


class PlaybookLaneError(PermissionError):
    """Raised when a lane other than the assignment's own lane tries to act on it."""


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


def create_assignment(
    playbook: PlaybookDefinition, role: str, requested_parameters: dict[str, Any], now: float | None = None
) -> dict[str, Any]:
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
        now_ts = now if now is not None else time.time()
        record = {
            "assignment_id": _NEXT_ID,
            "playbook_id": playbook.playbook_id,
            "pin": playbook.pin,
            "role": role,
            "parameters": resolved,
            "state": PlaybookAssignmentState.ASSIGNED.value,
            "lane_id": None,
            "created_at": now_ts,
            "last_seen_at": now_ts,
            "events": [],
        }
        _ASSIGNMENTS.append(record)
        return dict(record)


def _find_or_raise(assignment_id: int) -> dict[str, Any]:
    for record in _ASSIGNMENTS:
        if record["assignment_id"] == assignment_id:
            return record
    raise KeyError(f"no playbook assignment with id {assignment_id!r}")


def _require_current_version(record: dict[str, Any], version_hash: str) -> None:
    if version_hash != record["pin"]:
        raise PlaybookValidationError(
            f"stale playbook version hash for assignment {record['assignment_id']!r}: "
            f"expected pin {record['pin']!r}, got {version_hash!r}"
        )


def fetch_assignment(assignment_id: int, lane_id: str | None = None, now: float | None = None) -> dict[str, Any]:
    """Look up an assignment; a lane fetching its own in-flight assignment also counts as a heartbeat."""
    with _LOCK:
        record = _find_or_raise(assignment_id)
        if lane_id is not None and record["lane_id"] is not None:
            if lane_id != record["lane_id"]:
                raise PlaybookLaneError(f"lane {lane_id!r} does not own assignment {assignment_id!r}")
            record["last_seen_at"] = now if now is not None else time.time()
        return dict(record)


def acknowledge_assignment(
    assignment_id: int, lane_id: str, version_hash: str, now: float | None = None
) -> dict[str, Any]:
    """Transition ASSIGNED -> ACKNOWLEDGED, claiming the lane and stamping the version hash."""
    with _LOCK:
        record = _find_or_raise(assignment_id)
        validate_assignment_transition(record["state"], _S.ACKNOWLEDGED)
        _require_current_version(record, version_hash)
        now_ts = now if now is not None else time.time()
        record["state"] = _S.ACKNOWLEDGED.value
        record["lane_id"] = lane_id
        record["last_seen_at"] = now_ts
        event = {"state": _S.ACKNOWLEDGED.value, "at": now_ts, "lane_id": lane_id, "version_hash": version_hash}
        record["events"].append(event)
        return dict(record)


def complete_assignment(
    assignment_id: int, lane_id: str, version_hash: str, outcome: str, now: float | None = None
) -> dict[str, Any]:
    """Transition ACKNOWLEDGED/RUNNING -> COMPLETED/FAILED, stamping the version hash on the report."""
    with _LOCK:
        record = _find_or_raise(assignment_id)
        if record["lane_id"] is not None and lane_id != record["lane_id"]:
            raise PlaybookLaneError(f"lane {lane_id!r} does not own assignment {assignment_id!r}")
        target = validate_assignment_transition(record["state"], outcome)
        _require_current_version(record, version_hash)
        now_ts = now if now is not None else time.time()
        record["state"] = target.value
        record["last_seen_at"] = now_ts
        record["events"].append({"state": target.value, "at": now_ts, "lane_id": lane_id, "version_hash": version_hash})
        return dict(record)


_SILENT_STATES = (PlaybookAssignmentState.ACKNOWLEDGED.value, PlaybookAssignmentState.RUNNING.value)


def sweep_assignments(now: float, silent_after_seconds: float, abandon_after_seconds: float) -> dict[str, int]:
    """Single O(N) pass over all assignments: silent acknowledged/running lanes -> TIMED_OUT,
    never-acknowledged assignments abandoned past their window -> ORPHANED."""
    swept = {"timed_out": 0, "orphaned": 0}
    with _LOCK:
        for record in _ASSIGNMENTS:
            state = record["state"]
            if state == _S.ASSIGNED.value and now - record["created_at"] >= abandon_after_seconds:
                target, key = _S.ORPHANED, "orphaned"
            elif state in _SILENT_STATES and now - record["last_seen_at"] >= silent_after_seconds:
                target, key = _S.TIMED_OUT, "timed_out"
            else:
                continue
            validate_assignment_transition(state, target)
            record["state"] = target.value
            record["last_seen_at"] = now
            record["events"].append(
                {"state": target.value, "at": now, "lane_id": record["lane_id"], "version_hash": None}
            )
            swept[key] += 1
    return swept
