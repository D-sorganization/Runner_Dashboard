"""Private Mode PM-10: admission policy, per-level lanes, and explicit states (issue #3261, epic #3249)."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from private_context_audit import PrivateModeAuditEvent
from private_mode import (
    DISABLED,
    ClassificationEnvelope,
    ClassificationError,
    Level,
    PrivateModeConfig,
    stamp_envelope,
    validate_envelope,
)
from staff.adapter_policies import PERMISSION_BYPASS_FLAGS
from staff.cloud_dispatch import normalize_provider

_SANITIZED_REASON_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
_VALID_MODES: frozenset[str] = frozenset({"local", "cloud"})
_LANE_KEYS = frozenset(
    {"max_concurrent", "allowed_agents", "allowed_destinations", "allowed_modes", "requires_cloud_approval"}
)
_POLICY_KEYS: frozenset[str] = frozenset({"lanes", "default_owner", "default_deadline_seconds"})


class AdmissionState(StrEnum):
    READY, RUNNING = "READY", "RUNNING"
    PARKED_POLICY, PARKED_CAPACITY, PARKED_AUDIT = "PARKED_POLICY", "PARKED_CAPACITY", "PARKED_AUDIT"
    FAILED, CANCELLED = "FAILED", "CANCELLED"


PARKED_STATES = frozenset({AdmissionState.PARKED_POLICY, AdmissionState.PARKED_CAPACITY, AdmissionState.PARKED_AUDIT})
TERMINAL_STATES: frozenset[AdmissionState] = frozenset({AdmissionState.FAILED, AdmissionState.CANCELLED})
_UNPARKED_STATES: frozenset[AdmissionState] = frozenset({AdmissionState.READY, AdmissionState.RUNNING})


def validate_sanitized_reason(reason: str, *, allow_empty: bool = False) -> str:
    """Validate that *reason* is a short, sanitized category slug with no raw content."""
    if not isinstance(reason, str):
        raise ClassificationError("admission: sanitized reason must be a str")
    cleaned = reason.strip()
    if not cleaned:
        if allow_empty:
            return ""
        raise ClassificationError("admission: sanitized reason must not be empty")
    if not _SANITIZED_REASON_RE.match(cleaned):
        raise ClassificationError("admission: sanitized reason must match ^[a-z0-9][a-z0-9._:-]{0,63}$")
    return cleaned


@dataclass(frozen=True)
class LaneAdmissionPolicy:
    max_concurrent: int
    allowed_agents: frozenset[str]
    allowed_destinations: frozenset[str]
    allowed_modes: frozenset[str]
    requires_cloud_approval: bool


@dataclass(frozen=True)
class AdmissionPolicy:
    lanes: Mapping[Level, LaneAdmissionPolicy]
    default_owner: str
    default_deadline_seconds: int


@dataclass(frozen=True)
class AdmissionRequest:
    job_id: str
    repo: str
    envelope: ClassificationEnvelope | Mapping[str, Any]
    agent: str
    provider: str
    execution_mode: str = "local"
    argv: Sequence[str] = ()
    issue: int | None = None
    paths: Sequence[str] = ()
    actor: str = "dispatcher"
    owner: str | None = None
    cloud_approved: bool = False
    keys_ready: bool = True


@dataclass(frozen=True)
class AdmissionDecision:
    job_id: str
    state: AdmissionState
    effective_level: Level
    actual_destination: str
    reason: str = ""
    owner: str = ""
    deadline: str | None = None
    spawn_handle: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdmissionState):
            raise ClassificationError("AdmissionDecision: state must be an AdmissionState")
        validate_sanitized_reason(self.reason, allow_empty=self.state in _UNPARKED_STATES)
        if self.state in PARKED_STATES and (not self.owner.strip() or not self.deadline or not self.deadline.strip()):
            raise ClassificationError("AdmissionDecision: parked state requires non-empty owner and deadline")


class AuditWriterProtocol(Protocol):
    def record_decision(self, event: Any, *, config: PrivateModeConfig = DISABLED) -> Any: ...


class ExclusionCheckerProtocol(Protocol):
    def blocking(self, repo: str, issue: int | None = None, paths: Sequence[str] = (), now: Any = None) -> Any: ...


def _parse_str_set(raw: object, where: str) -> frozenset[str]:
    if not isinstance(raw, list) or not raw or not all(isinstance(x, str) and x.strip() for x in raw):
        raise ClassificationError(f"{where}: must be a non-empty list of non-empty strings")
    return frozenset(x.strip().lower() for x in raw)


def parse_admission_policy(raw: object) -> AdmissionPolicy:
    """Validate and construct an :class:`AdmissionPolicy` from overlay configuration."""
    if not isinstance(raw, Mapping) or set(raw) != _POLICY_KEYS:
        raise ClassificationError(f"admission_policy: keys must match {sorted(_POLICY_KEYS)}")
    default_owner, deadline_sec, raw_lanes = raw["default_owner"], raw["default_deadline_seconds"], raw["lanes"]
    if not isinstance(default_owner, str) or not default_owner.strip():
        raise ClassificationError("admission_policy.default_owner: must be a non-empty str")
    if isinstance(deadline_sec, bool) or not isinstance(deadline_sec, int) or deadline_sec < 1:
        raise ClassificationError("admission_policy.default_deadline_seconds: must be a positive int")
    if not isinstance(raw_lanes, Mapping) or set(raw_lanes) != {lvl.name for lvl in Level}:
        raise ClassificationError("admission_policy.lanes: must define PUBLIC, INTERNAL, and SENSITIVE")

    lanes: dict[Level, LaneAdmissionPolicy] = {}
    for lvl in Level:
        entry, where = raw_lanes[lvl.name], f"admission_policy.lanes.{lvl.name}"
        if not isinstance(entry, Mapping) or set(entry) != _LANE_KEYS:
            raise ClassificationError(f"{where}: keys must match {sorted(_LANE_KEYS)}")
        max_c, req_cloud = entry["max_concurrent"], entry["requires_cloud_approval"]
        if isinstance(max_c, bool) or not isinstance(max_c, int) or max_c < 1 or not isinstance(req_cloud, bool):
            raise ClassificationError(f"{where}: invalid max_concurrent or requires_cloud_approval")
        modes = _parse_str_set(entry["allowed_modes"], f"{where}.allowed_modes")
        if not modes.issubset(_VALID_MODES):
            raise ClassificationError(f"{where}.allowed_modes: unsupported mode")
        agents = _parse_str_set(entry["allowed_agents"], f"{where}.allowed_agents")
        dests = _parse_str_set(entry["allowed_destinations"], f"{where}.allowed_destinations")
        lanes[lvl] = LaneAdmissionPolicy(max_c, agents, dests, modes, req_cloud)
    return AdmissionPolicy(lanes=lanes, default_owner=default_owner.strip(), default_deadline_seconds=deadline_sec)


def resolve_actual_destination(provider: str, execution_mode: str, argv: Sequence[str] = ()) -> tuple[str, str]:
    """Resolve ``(actual_mode, actual_destination)`` from provider, declared mode, and launch argv."""
    norm_provider, norm_mode = normalize_provider(provider), execution_mode.strip().lower()
    if norm_mode not in _VALID_MODES:
        raise ClassificationError("resolve_actual_destination: execution_mode must be 'local' or 'cloud'")
    tokens = tuple(str(arg).strip() for arg in argv)
    gh_disp = len(tokens) >= 3 and tokens[:2] == ("gh", "api") and "dispatches" in tokens[2]
    is_cloud = "--remote" in tokens or (len(tokens) >= 2 and tokens[:2] == ("codex", "cloud")) or gh_disp
    actual_mode = "cloud" if (norm_mode == "cloud" or is_cloud) else "local"
    return actual_mode, f"{actual_mode}:{norm_provider}"


def has_permission_bypass_flag(argv: Sequence[str]) -> bool:
    """Return True if *argv* contains any flag or value in :data:`PERMISSION_BYPASS_FLAGS`."""
    for token in argv:
        cleaned = str(token).strip()
        flag, _, val = cleaned.partition("=")
        if cleaned in PERMISSION_BYPASS_FLAGS or flag in PERMISSION_BYPASS_FLAGS or val in PERMISSION_BYPASS_FLAGS:
            return True
    return False


class PrivateModeAdmissionController:
    """Per-level admission controller enforcing PM-10 policy, pre-spawn checks, and lane isolation."""

    def __init__(
        self,
        *,
        policy: AdmissionPolicy,
        config: PrivateModeConfig = DISABLED,
        audit_store: AuditWriterProtocol | None = None,
        exclusions: ExclusionCheckerProtocol | None = None,
    ) -> None:
        if not isinstance(policy, AdmissionPolicy) or not isinstance(config, PrivateModeConfig):
            raise ClassificationError("PrivateModeAdmissionController: invalid policy or config")
        self._policy, self._config, self._audit_store, self._exclusions = policy, config, audit_store, exclusions
        self._lock = threading.RLock()
        self._running_by_level: dict[Level, set[str]] = {lvl: set() for lvl in Level}
        self._decisions: dict[str, AdmissionDecision] = {}

    def _deadline_iso(self, now: datetime | None) -> str:
        base = now or datetime.now(UTC)
        base = base.replace(tzinfo=UTC) if base.tzinfo is None else base
        return (base + timedelta(seconds=self._policy.default_deadline_seconds)).isoformat().replace("+00:00", "Z")

    def _check_policy_reason(
        self, req: AdmissionRequest, level: Level, actual_mode: str, actual_dest: str, now: datetime | None
    ) -> tuple[str, str] | None:
        owner = (req.owner or self._policy.default_owner).strip()
        if self._exclusions is not None and req.repo:
            blocking = self._exclusions.blocking(req.repo, req.issue, req.paths, now=now)
            if blocking is not None:
                return "dispatch-excluded", getattr(blocking, "owner", "") or owner
        if has_permission_bypass_flag(req.argv):
            return "adapter-policy-bypass-flag", owner
        lane = self._policy.lanes[level]
        need_approval = actual_mode == "cloud" and lane.requires_cloud_approval and not req.cloud_approved
        checks = (
            (req.agent.strip().lower() not in lane.allowed_agents, "agent-not-allowlisted"),
            (actual_mode not in lane.allowed_modes, "mode-not-allowlisted"),
            (actual_dest not in lane.allowed_destinations, "destination-not-allowlisted"),
            (need_approval, "cloud-approval-required"),
            (level > Level.PUBLIC and not req.keys_ready, "keys-unavailable"),
        )
        return next(((reason, owner) for blocked, reason in checks if blocked), None)

    def admit(self, req: AdmissionRequest, *, now: datetime | None = None) -> AdmissionDecision:
        """Evaluate pre-spawn checks and lane capacity without spawning a CLI."""
        if not isinstance(req, AdmissionRequest) or not req.job_id.strip():
            raise ClassificationError("admit: job_id must be a non-empty str")
        env = validate_envelope(req.envelope)
        stamped = stamp_envelope(env.level, hop="admission", config=self._config).level
        eff = stamped if self._config.enabled else Level.PUBLIC
        actual_mode, actual_dest = resolve_actual_destination(req.provider, req.execution_mode, req.argv)
        if not self._config.enabled:
            dec = AdmissionDecision(req.job_id, AdmissionState.READY, Level.PUBLIC, actual_dest)
            with self._lock:
                self._decisions[req.job_id] = dec
            return dec

        owner = (req.owner or self._policy.default_owner).strip()
        policy_block = self._check_policy_reason(req, eff, actual_mode, actual_dest, now)
        if policy_block is not None:
            return self._finalize(req, AdmissionState.PARKED_POLICY, eff, actual_dest, *policy_block, now)

        with self._lock:
            running = self._running_by_level[eff]
            if req.job_id not in running and len(running) >= self._policy.lanes[eff].max_concurrent:
                state_cap = AdmissionState.PARKED_CAPACITY
                return self._finalize(req, state_cap, eff, actual_dest, "lane-capacity-exhausted", owner, now)
        return self._finalize(req, AdmissionState.READY, eff, actual_dest, "admitted", owner, now)

    def _finalize(
        self, req: AdmissionRequest, state: AdmissionState, level: Level, dest: str, reason: str, owner: str, now: Any
    ) -> AdmissionDecision:
        deadline = self._deadline_iso(now)
        if self._audit_store is not None:
            ts = (now or datetime.now(UTC)).isoformat().replace("+00:00", "Z")
            meta: dict[str, str | int | bool] = {"destination": dest, "reason": reason}
            ev = PrivateModeAuditEvent("admission", req.actor, req.job_id, level, state.value, meta, ts)
            audit_res = self._audit_store.record_decision(ev, config=self._config)
            if bool(getattr(audit_res, "parked", False)):
                a_reason = validate_sanitized_reason(str(getattr(audit_res, "reason", "")) or "audit-unavailable")
                dec = AdmissionDecision(req.job_id, AdmissionState.PARKED_AUDIT, level, dest, a_reason, owner, deadline)
                with self._lock:
                    self._decisions[req.job_id] = dec
                return dec
        p_args = (validate_sanitized_reason(reason), owner, deadline) if state in PARKED_STATES else ()
        dec = AdmissionDecision(req.job_id, state, level, dest, *p_args)
        with self._lock:
            self._decisions[req.job_id] = dec
        return dec

    def admit_and_spawn(
        self, req: AdmissionRequest, *, spawner: Callable[[AdmissionRequest], Any], now: datetime | None = None
    ) -> AdmissionDecision:
        """Run :meth:`admit`; only if ``READY`` reserve the per-level slot, invoke *spawner*, and mark ``RUNNING``."""
        with self._lock:
            dec = self.admit(req, now=now)
            if dec.state is not AdmissionState.READY:
                return dec
            self._running_by_level[dec.effective_level].add(req.job_id)
        try:
            handle = spawner(req)
        except Exception:
            with self._lock:
                self._running_by_level[dec.effective_level].discard(req.job_id)
            raise
        eff_l, dest = dec.effective_level, dec.actual_destination
        run_dec = AdmissionDecision(req.job_id, AdmissionState.RUNNING, eff_l, dest, spawn_handle=handle)
        with self._lock:
            self._decisions[req.job_id] = run_dec
        return run_dec

    def transition(
        self, job_id: str, target_state: AdmissionState, *, reason: str = "", owner: str | None = None, now: Any = None
    ) -> AdmissionDecision:
        """Transition a tracked job to *target_state*, releasing its lane slot on terminal or parked states."""
        if not isinstance(target_state, AdmissionState):
            raise ClassificationError("transition: target_state must be an AdmissionState")
        with self._lock:
            prior = self._decisions.get(job_id)
            if prior is None:
                raise ClassificationError("transition: unknown job_id")
            if target_state is not AdmissionState.RUNNING:
                self._running_by_level[prior.effective_level].discard(job_id)
            else:
                self._running_by_level[prior.effective_level].add(job_id)
            parked = target_state in PARKED_STATES
            eff_owner = (owner or prior.owner or self._policy.default_owner).strip() if parked else ""
            deadline = self._deadline_iso(now) if parked else None
            sanitized = validate_sanitized_reason(reason, allow_empty=target_state in _UNPARKED_STATES)
            eff_l, dest = prior.effective_level, prior.actual_destination
            updated = AdmissionDecision(job_id, target_state, eff_l, dest, sanitized, eff_owner, deadline)
            self._decisions[job_id] = updated
            return updated
