"""Approved rd-threads relay and host-flip snapshots (#3100 / #3064 envelope)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from staff.role_sync_capabilities import CapabilityDecline, HostKind

_HOLDS_NOTE = "Holds are immutable and are not copied during host flip."


class RdThreadsRelay(Protocol):
    """Narrow #3072 client surface used for approved capability-miss relay."""

    def send_rd_threads(self, envelope: Mapping[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PendingQuestion:
    correlation_id: str
    summary: str
    options: tuple[dict[str, Any], ...]
    recommended: str
    task_state: str = "input-required"

    def to_dict(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "summary": self.summary,
            "options": [dict(o) for o in self.options],
            "recommended": self.recommended,
            "task_state": self.task_state,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> PendingQuestion:
        opts = raw.get("options") or []
        return cls(
            correlation_id=str(raw.get("correlation_id") or ""),
            summary=str(raw.get("summary") or ""),
            options=tuple(dict(o) for o in opts if isinstance(o, Mapping)),
            recommended=str(raw.get("recommended") or ""),
            task_state=str(raw.get("task_state") or "input-required"),
        )


@dataclass(frozen=True)
class HostFlipSnapshot:
    role: str
    source_host: HostKind
    target_host: HostKind
    permitted_state: dict[str, Any]
    pending_questions: tuple[PendingQuestion, ...]
    in_flight_work: tuple[dict[str, Any], ...]
    holds_immutable_note: str = _HOLDS_NOTE
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "role": self.role,
            "source_host": self.source_host.value,
            "target_host": self.target_host.value,
            "permitted_state": dict(self.permitted_state),
            "pending_questions": [q.to_dict() for q in self.pending_questions],
            "in_flight_work": [dict(w) for w in self.in_flight_work],
            "holds_immutable_note": self.holds_immutable_note,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> HostFlipSnapshot:
        raw_q = raw.get("pending_questions") or []
        questions = tuple(PendingQuestion.from_dict(q) for q in raw_q if isinstance(q, Mapping))
        work = tuple(dict(w) for w in raw.get("in_flight_work") or [] if isinstance(w, Mapping))
        return cls(
            role=str(raw.get("role") or ""),
            source_host=HostKind(str(raw.get("source_host") or HostKind.GROKBOT.value)),
            target_host=HostKind(str(raw.get("target_host") or HostKind.RUNNER_DASHBOARD.value)),
            permitted_state=dict(raw.get("permitted_state") or {}),
            pending_questions=questions,
            in_flight_work=work,
            holds_immutable_note=str(raw.get("holds_immutable_note") or _HOLDS_NOTE),
            version=int(raw.get("version") or 1),
        )


def snapshot_for_host_flip(
    *,
    role: str,
    source_host: HostKind,
    target_host: HostKind,
    permitted_state: Mapping[str, Any],
    pending_questions: Sequence[PendingQuestion],
    in_flight_work: Sequence[Mapping[str, Any]],
) -> HostFlipSnapshot:
    """Capture permitted runtime state for a host flip (no holds, no household payloads)."""
    if not role.strip():
        raise ValueError("role is required for host flip snapshot")
    if source_host == target_host:
        raise ValueError("host flip requires distinct source and target hosts")
    safe_state = {k: v for k, v in permitted_state.items() if not str(k).lower().startswith("hold")}
    return HostFlipSnapshot(
        role=role.strip(),
        source_host=source_host,
        target_host=target_host,
        permitted_state=dict(safe_state),
        pending_questions=tuple(pending_questions),
        in_flight_work=tuple(dict(w) for w in in_flight_work),
    )


def restore_host_flip_snapshot(snapshot: HostFlipSnapshot) -> HostFlipSnapshot:
    """Round-trip through the serialisable dict form (post: structure preserved)."""
    return HostFlipSnapshot.from_dict(snapshot.to_dict())


def _dedupe_key(payload: Mapping[str, Any], now: float) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{canonical}|{int(now // 60)}".encode()).hexdigest()


def _build_relay_envelope(
    *,
    decline: CapabilityDecline,
    from_agent: str,
    to_role: str,
    now: float,
) -> dict[str, Any]:
    summary = f"Capability handoff: {decline.category.value}"
    body = decline.notice
    payload = {
        "kind": "update",
        "from": from_agent,
        "to": to_role,
        "summary": summary,
        "body": body,
        "urgency": "normal",
        "importance": "normal",
        "ack_required": False,
        "task_state": "submitted",
        "meta": {
            "role_sync": {
                "reason": decline.code,
                "category": decline.category.value,
                "active_host": decline.active_host.value,
                "peer_host": (decline.peer_host or decline.active_host).value,
            }
        },
    }
    key = _dedupe_key(payload, now)[:24]
    return {"id": f"m_{key}", "correlation_id": f"c_{key}", **payload}


def relay_capability_miss(
    client: RdThreadsRelay,
    *,
    decline: CapabilityDecline,
    from_agent: str,
    to_role: str,
    now: float,
) -> dict[str, Any]:
    """Relay to the peer host only when handoff is explicitly approved (#3064 / #3072)."""
    assert from_agent.strip() and to_role.strip(), "relay requires from_agent and to_role"
    if not decline.relay_eligible:
        return {"ok": False, "error": "relay_not_approved", "notice": decline.notice}
    envelope = _build_relay_envelope(decline=decline, from_agent=from_agent, to_role=to_role, now=now)
    return client.send_rd_threads(envelope)
