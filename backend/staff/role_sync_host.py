"""Per-role/host capabilities, visible decline, rd-threads relay, host-flip snapshot (#3100)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

_HOST_GROKBOT, _HOST_RD = "grok-bot", "runner-dashboard"
_HOLDS_NOTE = "Holds are immutable and are not copied during host flip."


class HostKind(str, Enum):
    GROKBOT = "grokbot"
    RUNNER_DASHBOARD = "runner_dashboard"


class CapabilityCategory(str, Enum):
    MAIL_CONNECTOR = "mail_connector"
    CALENDAR_CONNECTOR = "calendar_connector"
    DRIVE_CONNECTOR = "drive_connector"
    CREDENTIAL_VAULT = "credential_vault"
    ROUTINES = "routines"
    CHAT_QUESTION_CARDS = "chat_question_cards"
    PERSISTENT_CLI_SESSIONS = "persistent_cli_sessions"
    RUNNERS = "runners"
    STAFF_API = "staff_api"
    HOUSEHOLD_PERSONAL_DATA = "household_personal_data"


_GROKBOT_ONLY = frozenset(
    {
        CapabilityCategory.MAIL_CONNECTOR,
        CapabilityCategory.CALENDAR_CONNECTOR,
        CapabilityCategory.DRIVE_CONNECTOR,
        CapabilityCategory.CREDENTIAL_VAULT,
        CapabilityCategory.ROUTINES,
        CapabilityCategory.CHAT_QUESTION_CARDS,
    }
)
_RD_ONLY = frozenset(
    {
        CapabilityCategory.PERSISTENT_CLI_SESSIONS,
        CapabilityCategory.RUNNERS,
        CapabilityCategory.STAFF_API,
    }
)


@dataclass(frozen=True)
class RoleSyncConfig:
    relay_handoff_approved: bool = False
    household_data_opt_in: bool = False
    preferred_host: HostKind = HostKind.GROKBOT


@dataclass(frozen=True)
class CapabilityDecline:
    notice: str
    category: CapabilityCategory
    role: str
    active_host: HostKind
    peer_host: HostKind | None
    relay_eligible: bool
    code: str = "capability_unavailable"


class RdThreadsRelay(Protocol):
    def send_rd_threads(self, envelope: Mapping[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PendingQuestion:
    correlation_id: str
    summary: str
    options: tuple[dict[str, Any], ...]
    recommended: str
    task_state: str = "input-required"


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


def parse_role_sync_config(data: Mapping[str, Any] | None) -> RoleSyncConfig:
    if not isinstance(data, Mapping):
        return RoleSyncConfig()
    block = data.get("role_sync")
    if not isinstance(block, Mapping):
        return RoleSyncConfig()
    host_raw = str(block.get("preferred_host") or block.get("host") or "grokbot").lower()
    rd_hosts = ("rd", "runner_dashboard", "runner-dashboard")
    preferred = HostKind.RUNNER_DASHBOARD if host_raw in rd_hosts else HostKind.GROKBOT
    return RoleSyncConfig(
        relay_handoff_approved=block.get("relay_handoff_approved") is True,
        household_data_opt_in=block.get("household_data_opt_in") is True,
        preferred_host=preferred,
    )


def active_host_for_role(config: RoleSyncConfig) -> HostKind:
    return config.preferred_host


def _peer(host: HostKind) -> HostKind:
    return HostKind.RUNNER_DASHBOARD if host == HostKind.GROKBOT else HostKind.GROKBOT


def _host_label(host: HostKind) -> str:
    return _HOST_RD if host == HostKind.RUNNER_DASHBOARD else _HOST_GROKBOT


def host_has_capability(host: HostKind, category: CapabilityCategory, *, config: RoleSyncConfig | None = None) -> bool:
    cfg = config or RoleSyncConfig()
    if category == CapabilityCategory.HOUSEHOLD_PERSONAL_DATA:
        return host == HostKind.GROKBOT or (host == HostKind.RUNNER_DASHBOARD and cfg.household_data_opt_in)
    if category in _GROKBOT_ONLY:
        return host == HostKind.GROKBOT
    if category in _RD_ONLY:
        return host == HostKind.RUNNER_DASHBOARD
    return False


def decline_for_miss(
    *, role: str, category: CapabilityCategory, active_host: HostKind, config: RoleSyncConfig
) -> CapabilityDecline:
    assert role.strip(), "role is required"
    peer = _peer(active_host)
    if (
        category == CapabilityCategory.HOUSEHOLD_PERSONAL_DATA
        and active_host == HostKind.RUNNER_DASHBOARD
        and not config.household_data_opt_in
    ):
        return CapabilityDecline(
            notice=(
                f"Role '{role}' cannot use household personal data on {_host_label(active_host)} "
                "without an explicit household_data_opt_in flag."
            ),
            category=category,
            role=role,
            active_host=active_host,
            peer_host=None,
            relay_eligible=False,
        )
    if host_has_capability(active_host, category, config=config):
        raise ValueError("decline_for_miss called when capability is present")
    relay_ok = config.relay_handoff_approved and host_has_capability(peer, category, config=config)
    return CapabilityDecline(
        notice=(
            f"Role '{role}' cannot run {category.value.replace('_', ' ')} on {_host_label(active_host)}; "
            f"it is available on {_host_label(peer)}."
        ),
        category=category,
        role=role,
        active_host=active_host,
        peer_host=peer,
        relay_eligible=relay_ok,
    )


def _question_dict(q: PendingQuestion) -> dict[str, Any]:
    return {
        "correlation_id": q.correlation_id,
        "summary": q.summary,
        "options": [dict(o) for o in q.options],
        "recommended": q.recommended,
        "task_state": q.task_state,
    }


def _question_from(raw: Mapping[str, Any]) -> PendingQuestion:
    opts = raw.get("options") or []
    return PendingQuestion(
        correlation_id=str(raw.get("correlation_id") or ""),
        summary=str(raw.get("summary") or ""),
        options=tuple(dict(o) for o in opts if isinstance(o, Mapping)),
        recommended=str(raw.get("recommended") or ""),
        task_state=str(raw.get("task_state") or "input-required"),
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


def snapshot_to_dict(snapshot: HostFlipSnapshot) -> dict[str, Any]:
    return {
        "version": snapshot.version,
        "role": snapshot.role,
        "source_host": snapshot.source_host.value,
        "target_host": snapshot.target_host.value,
        "permitted_state": dict(snapshot.permitted_state),
        "pending_questions": [_question_dict(q) for q in snapshot.pending_questions],
        "in_flight_work": [dict(w) for w in snapshot.in_flight_work],
        "holds_immutable_note": snapshot.holds_immutable_note,
    }


def restore_host_flip_snapshot(snapshot: HostFlipSnapshot) -> HostFlipSnapshot:
    raw = snapshot_to_dict(snapshot)
    questions = tuple(_question_from(q) for q in raw.get("pending_questions") or [] if isinstance(q, Mapping))
    work = tuple(dict(w) for w in raw.get("in_flight_work") or [] if isinstance(w, Mapping))
    return HostFlipSnapshot(
        role=str(raw.get("role") or ""),
        source_host=HostKind(str(raw.get("source_host") or HostKind.GROKBOT.value)),
        target_host=HostKind(str(raw.get("target_host") or HostKind.RUNNER_DASHBOARD.value)),
        permitted_state=dict(raw.get("permitted_state") or {}),
        pending_questions=questions,
        in_flight_work=work,
        holds_immutable_note=str(raw.get("holds_immutable_note") or _HOLDS_NOTE),
        version=int(raw.get("version") or 1),
    )


def _dedupe_key(payload: Mapping[str, Any], now: float) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{canonical}|{int(now // 60)}".encode()).hexdigest()


def relay_capability_miss(
    client: RdThreadsRelay,
    *,
    decline: CapabilityDecline,
    from_agent: str,
    to_role: str,
    now: float,
) -> dict[str, Any]:
    assert from_agent.strip() and to_role.strip(), "relay requires from_agent and to_role"
    if not decline.relay_eligible:
        return {"ok": False, "error": "relay_not_approved", "notice": decline.notice}
    summary = f"Capability handoff: {decline.category.value}"
    payload = {
        "kind": "update",
        "from": from_agent,
        "to": to_role,
        "summary": summary,
        "body": decline.notice,
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
    return client.send_rd_threads({"id": f"m_{key}", "correlation_id": f"c_{key}", **payload})
