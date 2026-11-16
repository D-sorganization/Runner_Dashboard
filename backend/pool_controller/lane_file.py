"""Durable lane file schema written by launchers before spawn (#4279, C-10; ADR 0012 v2)."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypedDict

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows development path
    fcntl = None  # type: ignore[assignment]

from pool_controller.lane_state import LaneState, LaneTransitionError, validate_lane_transition

LANE_FILE_SCHEMA_VERSION_V1 = "pool-controller-lane.v1"
LANE_FILE_SCHEMA_VERSION_V2 = "pool-controller-lane.v2"
LANE_FILE_SCHEMA_VERSION = LANE_FILE_SCHEMA_VERSION_V2
_SUPPORTED_SCHEMA_VERSIONS = frozenset({LANE_FILE_SCHEMA_VERSION_V1, LANE_FILE_SCHEMA_VERSION_V2})


class LaneFileError(ValueError):
    """Raised when a lane file cannot be read or updated."""


class LaneClaimRef(TypedDict, total=False):
    repo: str
    issue: int
    fence: str
    expires_at: float


class LaneFileRecord(TypedDict, total=False):
    schema_version: str
    session: str
    role: str
    state: str
    pid: int | None
    provider: str | None
    model: str | None
    host: str | None
    pid_started_at: float | None
    push_id: str | None
    claim: LaneClaimRef | None
    deadline_at: float | None
    state_reason: str
    created_at: float | None
    updated_at: float


def lane_file_path(log_dir: Path, session: str) -> Path:
    return log_dir / f"{session}.lane.json"


def pool_lock_path(log_dir: Path) -> Path:
    return log_dir / "pool.lock"


@contextmanager
def _locked_pool_dir(log_dir: Path) -> Iterator[None]:
    """Exclusive flock on ``pool.lock`` for all lane file writes in this directory (K-18)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    lock_path = pool_lock_path(log_dir)
    with lock_path.open("a+", encoding="utf-8") as lock_fh:
        if fcntl is not None:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except (OSError, TypeError, ValueError):
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _normalize_lane_record(raw: dict[str, Any]) -> LaneFileRecord:
    schema = raw.get("schema_version")
    if schema not in _SUPPORTED_SCHEMA_VERSIONS:
        raise LaneFileError(f"unsupported lane file schema version {schema!r}")
    record: LaneFileRecord = {
        "schema_version": str(schema),
        "session": str(raw["session"]),
        "role": str(raw["role"]),
        "state": str(raw["state"]),
        "pid": raw.get("pid"),
        "updated_at": float(raw["updated_at"]),
    }
    if schema == LANE_FILE_SCHEMA_VERSION_V2:
        record.update(
            {
                "provider": raw.get("provider"),
                "model": raw.get("model"),
                "host": raw.get("host"),
                "pid_started_at": raw.get("pid_started_at"),
                "push_id": raw.get("push_id"),
                "claim": raw.get("claim"),
                "deadline_at": raw.get("deadline_at"),
                "state_reason": str(raw.get("state_reason") or ""),
                "created_at": raw.get("created_at"),
            },
        )
    else:
        record.update(
            {
                "provider": None,
                "model": None,
                "host": None,
                "pid_started_at": None,
                "push_id": None,
                "claim": None,
                "deadline_at": None,
                "state_reason": "",
                "created_at": None,
            },
        )
    return record


def _v2_payload(
    *,
    session: str,
    role: str,
    state: str,
    pid: int | None,
    created_at: float,
    push_id: str,
    host: str,
    provider: str | None,
    model: str | None,
    pid_started_at: float | None,
    claim: LaneClaimRef | None,
    deadline_at: float | None,
    state_reason: str,
    updated_at: float | None = None,
) -> dict[str, Any]:
    stamp = created_at if updated_at is None else updated_at
    return {
        "schema_version": LANE_FILE_SCHEMA_VERSION_V2,
        "session": session,
        "role": role,
        "state": state,
        "pid": pid,
        "provider": provider,
        "model": model,
        "host": host,
        "pid_started_at": pid_started_at,
        "push_id": push_id,
        "claim": claim,
        "deadline_at": deadline_at,
        "state_reason": state_reason,
        "created_at": created_at,
        "updated_at": stamp,
    }


def write_lane_file(
    log_dir: Path,
    session: str,
    role: str,
    state: LaneState | str,
    *,
    pid: int | None = None,
    push_id: str = "",
    host: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    pid_started_at: float | None = None,
    claim: LaneClaimRef | None = None,
    deadline_at: float | None = None,
    state_reason: str = "",
) -> Path:
    """Create or replace the lane file (idempotent initial write before spawn)."""
    st = state if isinstance(state, LaneState) else LaneState(str(state).strip())
    path = lane_file_path(log_dir, session)
    resolved_host = host if host is not None else socket.gethostname()
    now = time.time()
    record = _v2_payload(
        session=session,
        role=role,
        state=st.value,
        pid=pid,
        created_at=now,
        push_id=push_id,
        host=resolved_host,
        provider=provider,
        model=model,
        pid_started_at=pid_started_at,
        claim=claim,
        deadline_at=deadline_at,
        state_reason=state_reason,
    )
    with _locked_pool_dir(log_dir):
        _atomic_write_json(path, record)
    return path


def read_lane_file(path: Path) -> LaneFileRecord:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LaneFileError(f"cannot read lane file {path}") from exc
    if not isinstance(raw, dict):
        raise LaneFileError(f"unsupported lane file schema in {path}")
    return _normalize_lane_record(raw)


def transition_lane_file(
    log_dir: Path,
    session: str,
    target: LaneState | str,
    *,
    pid: int | None = None,
    pid_started_at: float | None = None,
    state_reason: str | None = None,
) -> Path:
    path = lane_file_path(log_dir, session)
    with _locked_pool_dir(log_dir):
        if not path.is_file():
            raise LaneFileError(f"missing lane file for session {session!r}")
        record = read_lane_file(path)
        try:
            current = LaneState(record["state"])
            new_state = validate_lane_transition(current, target)
        except (LaneTransitionError, ValueError) as exc:
            raise LaneFileError(f"transition {record['state']!r} -> {target!r}: {exc}") from exc

        created_at = record.get("created_at") or record["updated_at"]
        host = record.get("host") or socket.gethostname()
        new_pid = pid if pid is not None else record.get("pid")
        new_pid_started = pid_started_at
        if new_pid_started is None and pid is not None:
            new_pid_started = time.time()
        elif new_pid_started is None:
            new_pid_started = record.get("pid_started_at")

        payload = _v2_payload(
            session=record["session"],
            role=record["role"],
            state=new_state.value,
            pid=new_pid,
            created_at=float(created_at),
            updated_at=time.time(),
            push_id=record.get("push_id") or "",
            host=host,
            provider=record.get("provider"),
            model=record.get("model"),
            pid_started_at=new_pid_started,
            claim=record.get("claim"),
            deadline_at=record.get("deadline_at"),
            state_reason=state_reason if state_reason is not None else (record.get("state_reason") or ""),
        )
        _atomic_write_json(path, payload)
    return path
