"""Durable lane file schema written by launchers before spawn (#4279, C-10)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, TypedDict

from pool_controller.lane_state import LaneState, LaneTransitionError, validate_lane_transition

LANE_FILE_SCHEMA_VERSION = "pool-controller-lane.v1"


class LaneFileError(ValueError):
    """Raised when a lane file cannot be read or updated."""


class LaneFileRecord(TypedDict):
    schema_version: str
    session: str
    role: str
    state: str
    pid: int | None
    updated_at: float


def lane_file_path(log_dir: Path, session: str) -> Path:
    return log_dir / f"{session}.lane.json"


def write_lane_file(
    log_dir: Path,
    session: str,
    role: str,
    state: LaneState | str,
    *,
    pid: int | None = None,
) -> Path:
    """Create or replace the lane file (idempotent initial write before spawn)."""
    st = state if isinstance(state, LaneState) else LaneState(str(state).strip())
    path = lane_file_path(log_dir, session)
    record: LaneFileRecord = {
        "schema_version": LANE_FILE_SCHEMA_VERSION,
        "session": session,
        "role": role,
        "state": st.value,
        "pid": pid,
        "updated_at": time.time(),
    }
    path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return path


def read_lane_file(path: Path) -> LaneFileRecord:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LaneFileError(f"cannot read lane file {path}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != LANE_FILE_SCHEMA_VERSION:
        raise LaneFileError(f"unsupported lane file schema in {path}")
    return raw  # type: ignore[return-value]


def transition_lane_file(
    log_dir: Path,
    session: str,
    target: LaneState | str,
    *,
    pid: int | None = None,
) -> Path:
    path = lane_file_path(log_dir, session)
    if not path.is_file():
        raise LaneFileError(f"missing lane file for session {session!r}")
    record = read_lane_file(path)
    try:
        current = LaneState(record["state"])
        new_state = validate_lane_transition(current, target)
    except (LaneTransitionError, ValueError) as exc:
        raise LaneFileError(f"transition {record['state']!r} -> {target!r}: {exc}") from exc
    record["state"] = new_state.value
    record["updated_at"] = time.time()
    if pid is not None:
        record["pid"] = pid
    path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return path
