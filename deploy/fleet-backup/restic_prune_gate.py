#!/usr/bin/env python3
"""Restic prune/forget gate — two verified restore drills required (#4020)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

KIND_RESTORE_DRILL = "restore_drill"
KIND_READ_DATA_SUBSET = "read_data_subset"
REQUIRED_VERIFIED_RESTORE_DRILLS = 2

PersistFn = Callable[["MachineRecord"], None]
AlertFn = Callable[[str], None]
RunnerFn = Callable[[list[str]], int]
Opener = Callable[[Request, float], Any]


class PreconditionViolation(Exception):
    """Gate precondition failed — do not run restic prune/forget."""


class DrillFailedError(Exception):
    """Restore drill failed; pipeline halted after alerting the hub."""


@dataclass
class BackupEvent:
    kind: str
    verified: bool
    at: str
    failed: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BackupEvent:
        kind = str(raw.get("kind") or "")
        if kind not in (KIND_RESTORE_DRILL, KIND_READ_DATA_SUBSET):
            raise ValueError(f"unknown backup event kind {kind!r}")
        return cls(kind=kind, verified=bool(raw.get("verified")), at=str(raw.get("at") or ""), failed=bool(raw.get("failed")))


@dataclass
class MachineRecord:
    version: int
    machine: str
    events: list[BackupEvent] = field(default_factory=list)
    halted: bool = False
    halt_reason: str | None = None


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_record(raw: dict[str, Any]) -> MachineRecord:
    if int(raw.get("version") or 0) != 1:
        raise ValueError("unsupported machine record version")
    machine = str(raw.get("machine") or "").strip()
    if not machine:
        raise ValueError("machine record missing machine name")
    events_raw = raw.get("events") or []
    if not isinstance(events_raw, list):
        raise ValueError("events must be a list")
    events = [BackupEvent.from_dict(item) for item in events_raw if isinstance(item, dict)]
    return MachineRecord(
        version=1,
        machine=machine,
        events=events,
        halted=bool(raw.get("halted")),
        halt_reason=(str(raw["halt_reason"]) if raw.get("halt_reason") is not None else None),
    )


def verified_restore_drill_count(record: MachineRecord) -> int:
    return sum(
        1 for event in record.events if event.kind == KIND_RESTORE_DRILL and event.verified and not event.failed
    )


def precondition_restic_prune_or_forget(record: MachineRecord) -> None:
    if record.halted:
        raise PreconditionViolation(f"backup pipeline halted: {record.halt_reason or 'restore drill pipeline halted'}")
    count = verified_restore_drill_count(record)
    if count < REQUIRED_VERIFIED_RESTORE_DRILLS:
        raise PreconditionViolation(f"need {REQUIRED_VERIFIED_RESTORE_DRILLS} verified restore drills on record, have {count}")


def restic_mutation_requires_gate(argv: list[str]) -> bool:
    return len(argv) >= 2 and argv[0] == "restic" and argv[1] in {"forget", "prune"}


def _default_opener(req: Request, timeout: float) -> Any:
    return urlopen(req, timeout=timeout)  # noqa: S310


def post_hub_drill_failure_alert(
    *,
    hub_origin: str,
    machine: str,
    detail: str,
    token: str | None = None,
    opener: Opener = _default_opener,
    timeout: float = 10.0,
) -> None:
    url = f"{hub_origin.rstrip('/')}/api/fleet/backup/drill-failure"
    body = json.dumps({"machine": machine, "detail": detail}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, data=body, headers=headers, method="POST")
    try:
        with opener(req, timeout) as resp:
            resp.read()
    except HTTPError as exc:
        raise RuntimeError(f"hub drill alert HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"hub drill alert failed: {exc}") from exc


def run_restore_drill_once(
    record: MachineRecord,
    *,
    verify: Callable[[], bool],
    alert: AlertFn,
    persist: PersistFn,
) -> MachineRecord:
    if record.halted:
        raise DrillFailedError(record.halt_reason or "backup pipeline already halted")
    if not verify():
        detail = "restore drill verification failed"
        record.events.append(BackupEvent(kind=KIND_RESTORE_DRILL, verified=False, at=_now_iso(), failed=True))
        record.halted = True
        record.halt_reason = detail
        persist(record)
        message = f"fleet backup restore drill failed on {record.machine}: {detail}"
        alert(message)
        raise DrillFailedError(message)
    record.events.append(BackupEvent(kind=KIND_RESTORE_DRILL, verified=True, at=_now_iso()))
    persist(record)
    return record


def record_read_data_subset_check(record: MachineRecord, *, verified: bool, persist: PersistFn) -> MachineRecord:
    record.events.append(
        BackupEvent(kind=KIND_READ_DATA_SUBSET, verified=verified, at=_now_iso(), failed=not verified),
    )
    persist(record)
    return record


def run_gated_restic(record: MachineRecord, argv: list[str], *, runner: RunnerFn) -> int:
    if restic_mutation_requires_gate(argv):
        precondition_restic_prune_or_forget(record)
    return runner(argv)
