"""Machine backup record for fleet restic restore drills (#4013, #4020)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

log = logging.getLogger("fleet_backup.machine_record")

KIND_RESTORE_DRILL = "restore_drill"
KIND_READ_DATA_SUBSET = "read_data_subset"
REQUIRED_VERIFIED_RESTORE_DRILLS = 2

DEFAULT_RECORD_PATH = Path("~/.config/runner-dashboard/fleet_backup_machine_record.json")

PersistFn = Callable[["MachineRecord"], None]
AlertFn = Callable[[str], None]
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
        return cls(
            kind=kind,
            verified=bool(raw.get("verified")),
            at=str(raw.get("at") or ""),
            failed=bool(raw.get("failed")),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "verified": self.verified, "at": self.at}
        if self.failed:
            out["failed"] = True
        return out


@dataclass
class MachineRecord:
    version: int
    machine: str
    events: list[BackupEvent] = field(default_factory=list)
    halted: bool = False
    halt_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "machine": self.machine,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "events": [e.to_dict() for e in self.events],
        }


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_record(raw: dict[str, Any]) -> MachineRecord:
    version = int(raw.get("version") or 0)
    if version != 1:
        raise ValueError(f"unsupported machine record version {version!r}")
    machine = str(raw.get("machine") or "").strip()
    if not machine:
        raise ValueError("machine record missing machine name")
    events_raw = raw.get("events") or []
    if not isinstance(events_raw, list):
        raise ValueError("events must be a list")
    events = [BackupEvent.from_dict(item) for item in events_raw if isinstance(item, dict)]
    return MachineRecord(
        version=version,
        machine=machine,
        events=events,
        halted=bool(raw.get("halted")),
        halt_reason=(str(raw["halt_reason"]) if raw.get("halt_reason") is not None else None),
    )


def load_record(path: Path) -> MachineRecord:
    data = json.loads(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("machine record must be a JSON object")
    return parse_record(data)


def save_record(path: Path, record: MachineRecord) -> None:
    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_or_init_record(path: Path, machine: str) -> MachineRecord:
    try:
        record = load_record(path)
    except FileNotFoundError:
        record = MachineRecord(version=1, machine=machine)
        save_record(path, record)
        return record
    if record.machine != machine:
        raise ValueError(f"machine record is for {record.machine!r}, not {machine!r}")
    return record


def verified_restore_drill_count(record: MachineRecord) -> int:
    return sum(
        1
        for event in record.events
        if event.kind == KIND_RESTORE_DRILL and event.verified and not event.failed
    )


def precondition_restic_prune_or_forget(record: MachineRecord) -> None:
    if record.halted:
        reason = record.halt_reason or "restore drill pipeline halted"
        raise PreconditionViolation(f"backup pipeline halted: {reason}")
    count = verified_restore_drill_count(record)
    if count < REQUIRED_VERIFIED_RESTORE_DRILLS:
        raise PreconditionViolation(
            f"need {REQUIRED_VERIFIED_RESTORE_DRILLS} verified restore drills on record, have {count}"
        )


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
    ok = verify()
    if not ok:
        detail = "restore drill verification failed"
        record.events.append(
            BackupEvent(kind=KIND_RESTORE_DRILL, verified=False, at=_now_iso(), failed=True),
        )
        record.halted = True
        record.halt_reason = detail
        persist(record)
        message = f"fleet backup restore drill failed on {record.machine}: {detail}"
        alert(message)
        raise DrillFailedError(message)
    record.events.append(BackupEvent(kind=KIND_RESTORE_DRILL, verified=True, at=_now_iso()))
    persist(record)
    return record
