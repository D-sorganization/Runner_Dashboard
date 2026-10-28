"""Private Mode PM-11: bounded parking, EXPIRED state, depth/age metrics, and alerts (issue #3262, epic #3249)."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from private_context_audit import PrivateModeAuditEvent
from private_mode import DISABLED, ClassificationError, Level, PrivateModeConfig, stamp_envelope
from private_mode_admission import PARKED_STATES, AdmissionDecision, AuditWriterProtocol, validate_sanitized_reason
from private_mode_emit import SharedProjection, emit_shared

_BOUNDS_KEYS: frozenset[str] = frozenset({"max_depth_per_level", "max_age_seconds", "escalation_owner"})


class ParkingState(StrEnum):
    PARKED_POLICY, PARKED_CAPACITY, PARKED_AUDIT = "PARKED_POLICY", "PARKED_CAPACITY", "PARKED_AUDIT"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class ParkingBounds:
    max_depth_per_level: Mapping[Level, int]
    max_age_seconds: int
    escalation_owner: str


@dataclass(frozen=True)
class ParkedJobRecord:
    job_id: str
    repo: str
    level: Level
    state: ParkingState
    reason: str
    owner: str
    parked_at: str
    deadline: str
    expired_at: str | None = None
    escalated_to: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, ParkingState):
            raise ClassificationError("ParkedJobRecord: state must be a ParkingState")
        validate_sanitized_reason(self.reason)
        if not self.owner.strip() or not self.parked_at.strip() or not self.deadline.strip():
            raise ClassificationError("ParkedJobRecord: owner, parked_at, and deadline must be non-empty")
        if self.state is ParkingState.EXPIRED and (
            not self.expired_at or not self.expired_at.strip() or not self.escalated_to or not self.escalated_to.strip()
        ):
            raise ClassificationError("ParkedJobRecord: EXPIRED state requires expired_at and escalated_to")


@dataclass(frozen=True)
class ParkingMetrics:
    depth_by_level: Mapping[Level, int]
    oldest_age_seconds_by_level: Mapping[Level, float]
    expired_count_by_level: Mapping[Level, int]
    overflow_count_by_level: Mapping[Level, int]


def _coerce_utc(ts: datetime | None) -> datetime:
    base = ts or datetime.now(UTC)
    if not isinstance(base, datetime):
        raise ClassificationError("parking: timestamp must be a datetime instance")
    return base.replace(tzinfo=UTC) if base.tzinfo is None else base.astimezone(UTC)


def _format_iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def _parse_iso(raw: str) -> datetime:
    cleaned = raw.strip()
    dt = datetime.fromisoformat(cleaned[:-1] + "+00:00" if cleaned.endswith("Z") else cleaned)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def parse_parking_bounds(raw: object) -> ParkingBounds:
    """Validate and construct :class:`ParkingBounds` from overlay configuration (DbC)."""
    if not isinstance(raw, Mapping) or set(raw) != _BOUNDS_KEYS:
        raise ClassificationError(f"parking_bounds: keys must match {sorted(_BOUNDS_KEYS)}")
    raw_depths, max_age, esc_owner = raw["max_depth_per_level"], raw["max_age_seconds"], raw["escalation_owner"]
    if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age < 1:
        raise ClassificationError("parking_bounds.max_age_seconds: must be a positive int")
    if not isinstance(esc_owner, str) or not esc_owner.strip():
        raise ClassificationError("parking_bounds.escalation_owner: must be a non-empty str")
    validate_sanitized_reason(esc_owner)
    if not isinstance(raw_depths, Mapping) or set(raw_depths) != {lvl.name for lvl in Level}:
        raise ClassificationError("parking_bounds.max_depth_per_level: must define PUBLIC, INTERNAL, and SENSITIVE")

    depths: dict[Level, int] = {}
    for lvl in Level:
        val = raw_depths[lvl.name]
        if isinstance(val, bool) or not isinstance(val, int) or val < 1:
            raise ClassificationError(f"parking_bounds.max_depth_per_level.{lvl.name}: must be a positive int")
        depths[lvl] = val
    return ParkingBounds(max_depth_per_level=depths, max_age_seconds=max_age, escalation_owner=esc_owner.strip())


class BoundedParkingQueue:
    """Bounded per-level parking queue with visible ``EXPIRED`` state, metrics, and ``emit_shared`` alerts."""

    def __init__(
        self,
        *,
        bounds: ParkingBounds,
        config: PrivateModeConfig = DISABLED,
        audit_store: AuditWriterProtocol | None = None,
        alert_sink: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        if not isinstance(bounds, ParkingBounds) or not isinstance(config, PrivateModeConfig):
            raise ClassificationError("BoundedParkingQueue: invalid bounds or config")
        self._bounds, self._config, self._audit_store, self._alert_sink = bounds, config, audit_store, alert_sink
        self._lock = threading.RLock()
        self._active: dict[str, tuple[ParkedJobRecord, datetime, datetime]] = {}
        self._records: dict[str, ParkedJobRecord] = {}
        self._expired_counts: dict[Level, int] = dict.fromkeys(Level, 0)
        self._overflow_counts: dict[Level, int] = dict.fromkeys(Level, 0)

    def enqueue(
        self,
        decision: AdmissionDecision,
        *,
        repo: str,
        now: datetime | None = None,
        extra_alert_fields: Mapping[str, Any] | None = None,
    ) -> ParkedJobRecord:
        """Enqueue a parked :class:`AdmissionDecision` or immediately expire it if level depth is full."""
        if not isinstance(decision, AdmissionDecision) or decision.state not in PARKED_STATES:
            raise ClassificationError("enqueue: decision must be a parked AdmissionDecision")
        if not isinstance(repo, str) or not repo.strip():
            raise ClassificationError("enqueue: repo must be a non-empty str")
        now_dt = _coerce_utc(now)
        now_iso = _format_iso(now_dt)
        eff_level = decision.effective_level if self._config.enabled else Level.PUBLIC
        p_state = ParkingState(decision.state.value)
        max_dl = now_dt + timedelta(seconds=self._bounds.max_age_seconds)
        eff_dl = min(_parse_iso(decision.deadline), max_dl) if decision.deadline else max_dl
        dl_iso = _format_iso(eff_dl)

        if not self._config.enabled:
            rec = ParkedJobRecord(
                decision.job_id, repo.strip(), Level.PUBLIC, p_state, decision.reason, decision.owner, now_iso, dl_iso
            )
            with self._lock:
                self._records[decision.job_id] = rec
            return rec

        with self._lock:
            active_n = sum(1 for r, _, _ in self._active.values() if r.level is eff_level)
            if decision.job_id not in self._active and active_n >= self._bounds.max_depth_per_level[eff_level]:
                self._overflow_counts[eff_level] += 1
                base = ParkedJobRecord(
                    decision.job_id, repo.strip(), eff_level, p_state, decision.reason, decision.owner, now_iso, dl_iso
                )
                return self._expire_record(base, now_dt, "parking-depth-exceeded", active_n, 0, extra_alert_fields)
            rec = ParkedJobRecord(
                decision.job_id, repo.strip(), eff_level, p_state, decision.reason, decision.owner, now_iso, dl_iso
            )
            self._active[decision.job_id] = (rec, now_dt, eff_dl)
            self._records[decision.job_id] = rec
            return rec

    def sweep_expired(
        self, *, now: datetime | None = None, extra_alert_fields: Mapping[str, Any] | None = None
    ) -> tuple[ParkedJobRecord, ...]:
        """Transition parked records past ``max_age_seconds`` or ``deadline`` to visible ``EXPIRED``."""
        now_dt = _coerce_utc(now)
        if not self._config.enabled:
            return ()
        expired_out: list[ParkedJobRecord] = []
        with self._lock:
            for job_id, (rec, parked_dt, deadline_dt) in list(self._active.items()):
                age_sec = max(0.0, (now_dt - parked_dt).total_seconds())
                if age_sec >= self._bounds.max_age_seconds or now_dt >= deadline_dt:
                    del self._active[job_id]
                    depth_now = sum(1 for r, _, _ in self._active.values() if r.level is rec.level)
                    exp_rec = self._expire_record(
                        rec, now_dt, "parking-age-expired", depth_now, int(age_sec * 1000), extra_alert_fields
                    )
                    expired_out.append(exp_rec)
        return tuple(expired_out)

    def _expire_record(
        self,
        base: ParkedJobRecord,
        now_dt: datetime,
        reason: str,
        depth: int,
        age_ms: int,
        extra_alert_fields: Mapping[str, Any] | None,
    ) -> ParkedJobRecord:
        now_iso, esc = _format_iso(now_dt), self._bounds.escalation_owner
        exp_rec = ParkedJobRecord(
            base.job_id,
            base.repo,
            base.level,
            ParkingState.EXPIRED,
            validate_sanitized_reason(reason),
            base.owner,
            base.parked_at,
            base.deadline,
            now_iso,
            esc,
        )
        self._records[base.job_id] = exp_rec
        self._expired_counts[base.level] += 1
        if self._audit_store is not None:
            meta: dict[str, str | int | bool] = {"reason": exp_rec.reason, "escalated_to": esc, "depth": depth}
            ev = PrivateModeAuditEvent(
                "parking_expired", esc, base.job_id, base.level, ParkingState.EXPIRED.value, meta, now_iso
            )
            self._audit_store.record_decision(ev, config=self._config)
        self._emit_escalation_alert(exp_rec, depth=depth, age_ms=age_ms, extra_alert_fields=extra_alert_fields)
        return exp_rec

    def _emit_escalation_alert(
        self, rec: ParkedJobRecord, *, depth: int, age_ms: int, extra_alert_fields: Mapping[str, Any] | None
    ) -> SharedProjection:
        env = stamp_envelope(rec.level, hop="parking_alert", config=self._config)
        payload: dict[str, Any] = {
            "id": rec.job_id,
            "item_id": rec.job_id,
            "repo": rec.repo,
            "target_id": rec.escalated_to or self._bounds.escalation_owner,
            "state": "expired",
            "status": "error",
            "outcome": "expired",
            "kind": "warning",
            "count": depth,
            "duration_ms": age_ms,
            "attempt": 1,
            "degraded": True,
            "ts": rec.expired_at or rec.parked_at,
        }
        if extra_alert_fields is not None:
            if not isinstance(extra_alert_fields, Mapping):
                raise ClassificationError("extra_alert_fields: must be a Mapping")
            for k, v in extra_alert_fields.items():
                if k not in payload:
                    payload[str(k)] = v
        return emit_shared("alerts", payload, level=env, config=self._config, sink=self._alert_sink)

    def get_record(self, job_id: str) -> ParkedJobRecord | None:
        if not isinstance(job_id, str) or not job_id.strip():
            raise ClassificationError("get_record: job_id must be a non-empty str")
        with self._lock:
            return self._records.get(job_id.strip())

    def list_parked(self, level: Level | None = None) -> tuple[ParkedJobRecord, ...]:
        if level is not None and not isinstance(level, Level):
            raise ClassificationError("list_parked: level must be a Level or None")
        with self._lock:
            return tuple(r for r, _, _ in self._active.values() if level is None or r.level is level)

    def list_expired(self, level: Level | None = None) -> tuple[ParkedJobRecord, ...]:
        if level is not None and not isinstance(level, Level):
            raise ClassificationError("list_expired: level must be a Level or None")
        with self._lock:
            return tuple(
                r
                for r in self._records.values()
                if r.state is ParkingState.EXPIRED and (level is None or r.level is level)
            )

    def metrics(self, *, now: datetime | None = None) -> ParkingMetrics:
        now_dt = _coerce_utc(now)
        with self._lock:
            depths: dict[Level, int] = dict.fromkeys(Level, 0)
            oldest: dict[Level, float] = dict.fromkeys(Level, 0.0)
            for rec, parked_dt, _ in self._active.values():
                depths[rec.level] += 1
                age = max(0.0, (now_dt - parked_dt).total_seconds())
                if age > oldest[rec.level]:
                    oldest[rec.level] = age
            return ParkingMetrics(
                depth_by_level=depths,
                oldest_age_seconds_by_level=oldest,
                expired_count_by_level=dict(self._expired_counts),
                overflow_count_by_level=dict(self._overflow_counts),
            )
