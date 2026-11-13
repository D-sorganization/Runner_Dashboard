"""Persona role heartbeat, aggregate health with 2-reading hysteresis, and wake/resume envelopes (#3295 S3, #3296).

Unhealthy signals: ``missed_heartbeats`` (2 missed 10-min heartbeats), ``slot_late`` (cron slot >30 min late),
``chain_exhausted``, and ``hub_unreachable`` (2 failed outside probes >=10 min apart).
Contract (DbC): unknown/unreadable state fails closed to ``degraded`` (``["unknown_health"]``), never ``healthy``.
2-reading hysteresis in both directions governs transitions to ``unhealthy`` (``role_unhealthy`` alert) and back
to ``healthy`` (``resume`` update).
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts import rd_barb_notify as _notify  # noqa: E402
from scripts import rd_threads_envelope as _envelope  # noqa: E402

log = logging.getLogger("dashboard.staff.role_health")

HEARTBEAT_INTERVAL_S = 600.0
MISSED_HEARTBEATS_THRESHOLD = 2
SLOT_LATE_THRESHOLD_S = 1800.0
PROBE_MIN_SEPARATION_S = 600.0
HYSTERESIS_READINGS = 2
VALID_STATES = frozenset({"healthy", "degraded", "unhealthy"})


def default_role_health_path() -> Path:
    from staff.rd_barb_memory import rd_barb_state_dir  # noqa: PLC0415

    return rd_barb_state_dir() / "role-health.json"


def _check_ts(value: float | None, name: str = "now") -> float:
    ts = time.time() if value is None else float(value)
    if not math.isfinite(ts) or ts < 0:
        raise ValueError(f"{name} must be a non-negative finite timestamp")
    return ts


def build_role_unhealthy_envelope(
    role: str, *, reasons: Sequence[str], epoch: int = 1, now: float | None = None
) -> dict[str, Any]:
    """Build and seal a role_unhealthy alert envelope on the shared rd-threads schema (#3295 S3)."""
    norm = _notify.normalize_role(role)
    if int(epoch) < 1:
        raise ValueError("epoch must be >= 1")
    reason_list = [str(r) for r in reasons if str(r).strip()] or ["unknown_health"]
    reason_str, ep = ", ".join(reason_list), int(epoch)
    return _envelope.seal(
        {
            "kind": "alert",
            "code": "role_unhealthy",
            "role": norm,
            "sender_role": norm,
            "epoch": ep,
            "reasons": reason_list,
            "from": norm,
            "to": "barb",
            "urgency": "high",
            "importance": "high",
            "summary": f"Persona role {norm} is unhealthy ({reason_str})",
            "body": f"Role {norm} failed 2 consecutive health checks at epoch {ep}: {reason_str}.",
            "recommended_action": f"Wake dormant Grok fallback for persona:{norm} at epoch {ep + 1}.",
            "dedupe_key": f"role-unhealthy:{norm}:{ep}:{','.join(sorted(reason_list))}",
        },
        _check_ts(now),
    )


def build_role_resume_envelope(role: str, *, epoch: int = 1, now: float | None = None) -> dict[str, Any]:
    """Build and seal a resume envelope after 2 consecutive healthy readings (#3295 S3)."""
    norm, ep = _notify.normalize_role(role), int(epoch)
    if ep < 1:
        raise ValueError("epoch must be >= 1")
    return _envelope.seal(
        {
            "kind": "update",
            "code": "resume",
            "role": norm,
            "sender_role": norm,
            "epoch": ep,
            "from": norm,
            "to": "barb",
            "urgency": "normal",
            "importance": "normal",
            "summary": f"Persona role {norm} resumed healthy on RD (epoch {ep})",
            "body": f"Role {norm} passed 2 consecutive health checks on RD at epoch {ep}.",
            "dedupe_key": f"role-resume:{norm}:{ep}",
        },
        _check_ts(now),
    )


def notify_role_transition(
    envelope: Mapping[str, Any],
    *,
    config: Mapping[str, str] | None = None,
    state_path: Path | None = None,
    sender: _notify.Sender = _notify.http_send,
) -> dict[str, Any]:
    """Route a role_unhealthy or resume envelope through the generalized persona_notify path."""
    role = _notify.normalize_role(str(envelope.get("role") or envelope.get("sender_role") or "rd-barb"))
    return _notify.persona_notify(role, dict(envelope), config=config, state_path=state_path, sender=sender)


def _raw_unhealthy_reasons(entry: Mapping[str, Any], now: float) -> list[str]:
    last_hb = entry.get("last_heartbeat_at")
    if last_hb is None:
        return ["unknown_health"]
    reasons: list[str] = []
    if now - float(last_hb) >= HEARTBEAT_INTERVAL_S * MISSED_HEARTBEATS_THRESHOLD:
        reasons.append("missed_heartbeats")
    expected_slot, started_slot = entry.get("expected_slot_at"), entry.get("last_slot_started_at")
    if (
        expected_slot is not None
        and (started_slot is None or float(started_slot) < float(expected_slot))
        and now - float(expected_slot) >= SLOT_LATE_THRESHOLD_S
    ):
        reasons.append("slot_late")
    if bool(entry.get("chain_exhausted")):
        reasons.append("chain_exhausted")
    probes = [float(t) for t in (entry.get("failed_probe_times") or []) if isinstance(t, (int, float))]
    if len(probes) >= 2 and probes[-1] - probes[0] >= PROBE_MIN_SEPARATION_S:
        reasons.append("hub_unreachable")
    return reasons


def _apply_hysteresis(
    role: str, entry: dict[str, Any], reasons: list[str], now: float
) -> tuple[dict[str, Any], str | None, dict[str, Any] | None]:
    prev = str(entry.get("state") or "degraded")
    prev_state = prev if prev in VALID_STATES else "degraded"
    epoch = max(1, int(entry.get("epoch") or 1))
    transition: str | None = None
    envelope: dict[str, Any] | None = None
    if reasons:
        unhealthy_count = int(entry.get("consecutive_unhealthy") or 0) + 1
        entry.update(consecutive_unhealthy=unhealthy_count, consecutive_healthy=0)
        if unhealthy_count >= HYSTERESIS_READINGS:
            if prev_state != "unhealthy":
                transition = "role_unhealthy"
                envelope = build_role_unhealthy_envelope(role, reasons=reasons, epoch=epoch, now=now)
            entry["state"] = "unhealthy"
        else:
            entry["state"] = "unhealthy" if prev_state == "unhealthy" else "degraded"
    else:
        healthy_count = int(entry.get("consecutive_healthy") or 0) + 1
        entry.update(consecutive_healthy=healthy_count, consecutive_unhealthy=0)
        if prev_state == "unhealthy" and healthy_count < HYSTERESIS_READINGS:
            entry["state"] = "unhealthy"
        else:
            if prev_state == "unhealthy":
                transition = "resume"
                envelope = build_role_resume_envelope(role, epoch=epoch, now=now)
            entry["state"] = "healthy"
    entry.update(reasons=reasons, evaluated_at=now)
    return entry, transition, envelope


class RoleHealthStore:
    """File-backed per-role health store with 2-reading hysteresis in both directions."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = default_role_health_path() if path is None else path
        self._lock = threading.Lock()

    def record_heartbeat(
        self,
        role: str,
        *,
        epoch: int = 1,
        slot_started_at: float | None = None,
        expected_slot_at: float | None = None,
        chain_exhausted: bool | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        norm = _notify.normalize_role(role)
        if int(epoch) < 1:
            raise ValueError("epoch must be >= 1")
        clock = _check_ts(now)
        with self._lock:
            roles = (self._read() or {}).setdefault("roles", {})
            entry = {**dict(roles.get(norm) or {}), "role": norm, "epoch": int(epoch), "last_heartbeat_at": clock}
            entry["failed_probe_times"] = []
            if slot_started_at is not None:
                entry["last_slot_started_at"] = _check_ts(slot_started_at, "slot_started_at")
            if expected_slot_at is not None:
                entry["expected_slot_at"] = _check_ts(expected_slot_at, "expected_slot_at")
            if chain_exhausted is not None:
                entry["chain_exhausted"] = bool(chain_exhausted)
            entry, transition, env = _apply_hysteresis(norm, entry, _raw_unhealthy_reasons(entry, clock), clock)
            roles[norm] = entry
            self._write({"roles": roles})
        return {**entry, "transition": transition, "envelope": env}

    def record_hub_probe(self, role: str, *, ok: bool, now: float | None = None) -> dict[str, Any]:
        norm, clock = _notify.normalize_role(role), _check_ts(now)
        with self._lock:
            roles = (self._read() or {}).setdefault("roles", {})
            entry = dict(roles.get(norm) or {"role": norm, "epoch": 1, "last_heartbeat_at": clock})
            probes = [float(t) for t in (entry.get("failed_probe_times") or []) if isinstance(t, (int, float))]
            if ok:
                entry["failed_probe_times"] = []
            elif not probes or clock - probes[-1] >= PROBE_MIN_SEPARATION_S:
                probes.append(clock)
                entry["failed_probe_times"] = probes
            reasons = _raw_unhealthy_reasons(entry, clock)
            if not reasons and not ok:
                roles[norm] = entry
                self._write({"roles": roles})
                return {**entry, "reasons": [], "transition": None, "envelope": None}
            entry, transition, env = _apply_hysteresis(norm, entry, reasons, clock)
            roles[norm] = entry
            self._write({"roles": roles})
        return {**entry, "transition": transition, "envelope": env}

    def evaluate_role(self, role: str, *, now: float | None = None) -> dict[str, Any]:
        norm, clock = _notify.normalize_role(role), _check_ts(now)
        with self._lock:
            data = self._read()
            if data is None or norm not in (data.get("roles") or {}):
                return {
                    "role": norm,
                    "state": "degraded",
                    "reasons": ["unknown_health"],
                    "consecutive_unhealthy": 0,
                    "consecutive_healthy": 0,
                    "epoch": 1,
                    "transition": None,
                    "envelope": None,
                }
            roles = data["roles"]
            entry, transition, env = _apply_hysteresis(
                norm, dict(roles[norm]), _raw_unhealthy_reasons(roles[norm], clock), clock
            )
            roles[norm] = entry
            self._write({"roles": roles})
        return {**entry, "transition": transition, "envelope": env}

    def aggregate_status(self, *, roles: Sequence[str] | None = None, now: float | None = None) -> dict[str, Any]:
        clock = _check_ts(now)
        data = self._read()
        stored = list((data or {}).get("roles") or {}) if data is not None else []
        targets = [_notify.normalize_role(r) for r in roles] if roles else stored
        if not targets:
            return {"status": "degraded", "roles": {}, "checked_at": clock}
        snapshots = {r: self.evaluate_role(r, now=clock) for r in targets}
        states = {snap["state"] for snap in snapshots.values()}
        overall = "unhealthy" if "unhealthy" in states else ("degraded" if "degraded" in states else "healthy")
        return {"status": overall, "roles": snapshots, "checked_at": clock}

    def _read(self) -> dict[str, Any] | None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"roles": {}}
        except (OSError, ValueError) as exc:
            log.error("role health state unreadable: %s", type(exc).__name__)
            return None
        return raw if isinstance(raw, dict) and isinstance(raw.get("roles"), dict) else None

    def _write(self, state: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)
