"""Brick sentinel per-role health probe, 2-reading hysteresis, and push wake envelopes (#3295 S6).

Extends the out-of-band Brick sentinel (#3050) to per-role health (`GET /api/v1/staff/roles/health`):
unknown health fails closed to ``degraded``; 2 outside probes >=10 min apart confirm ``hub_unreachable``
(``decision_authority="grok"``); reachable role health uses 2-reading hysteresis (``decision_authority="rd"``).
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts import rd_barb_notify as _notify  # noqa: E402
from scripts import rd_threads_envelope as _envelope  # noqa: E402

log = logging.getLogger("dashboard.staff.sentinel_role_health")

PROBE_MIN_SEPARATION_S = 600.0
HYSTERESIS_READINGS = 2
BACKSTOP_INTERVAL_S = 4 * 3600.0
DEFAULT_TIMEOUT = 3.0
VALID_ROLE_STATES = frozenset({"healthy", "degraded", "unhealthy"})
VALID_AUTHORITIES = frozenset({"rd", "grok"})

Opener = Callable[[Request, float], Any]


def _default_opener(req: Request, timeout: float) -> Any:
    return urlopen(req, timeout=timeout)  # noqa: S310


def _check_ts(value: float | None, name: str = "now") -> float:
    ts = time.time() if value is None else float(value)
    if not math.isfinite(ts) or ts < 0:
        raise ValueError(f"{name} must be a non-negative finite timestamp")
    return ts


def _scrub(text: str, secret: str | None) -> str:
    return text.replace(secret, "<redacted>") if secret else text


def _probe_res(
    reachable: bool, cls: str, detail: str, status: str, roles: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {"reachable": reachable, "classification": cls, "detail": detail, "status": status, "roles": roles or {}}


def default_sentinel_role_health_path() -> Path:
    from staff.rd_barb_memory import rd_barb_state_dir  # noqa: PLC0415

    return rd_barb_state_dir() / "sentinel-role-health.json"


def probe_roles_health(
    hub_url: str,
    *,
    bearer_token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    opener: Opener = _default_opener,
) -> dict[str, Any]:
    """Probe ``GET {hub_url}/api/v1/staff/roles/health`` without leaking ``bearer_token``."""
    base = str(hub_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("hub_url must be a non-empty URL")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")
    token = (bearer_token or "").strip() or None
    headers = {"Accept": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})}
    try:
        with opener(Request(f"{base}/api/v1/staff/roles/health", headers=headers), float(timeout)) as resp:  # noqa: S310
            body = resp.read()
    except HTTPError as exc:
        return _probe_res(False, "http_error", f"HTTP {exc.code}", "unhealthy")
    except (URLError, TimeoutError, OSError) as exc:
        return _probe_res(False, "transport_down", _scrub(str(exc), token), "unhealthy")

    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return _probe_res(True, "invalid_payload", "non-JSON response", "degraded")
    if not isinstance(payload, dict) or not isinstance(payload.get("roles"), dict):
        return _probe_res(True, "invalid_payload", "unexpected shape", "degraded")

    roles: dict[str, dict[str, Any]] = {}
    for raw_role, raw_entry in payload["roles"].items():
        try:
            norm = _notify.normalize_role(str(raw_role))
        except ValueError:
            continue
        entry = raw_entry if isinstance(raw_entry, Mapping) else {}
        raw_state = str(entry.get("state") or "")
        state = raw_state if raw_state in VALID_ROLE_STATES else "degraded"
        raw_reasons = entry.get("reasons")
        reasons = (
            [_scrub(str(r), token) for r in raw_reasons if str(r).strip()]
            if isinstance(raw_reasons, Sequence) and not isinstance(raw_reasons, (str, bytes))
            else []
        )
        if state != "healthy" and not reasons:
            reasons = ["unknown_health"]
        roles[norm] = {"role": norm, "state": state, "reasons": reasons, "epoch": max(1, int(entry.get("epoch") or 1))}

    states = {r["state"] for r in roles.values()}
    overall = (
        "degraded"
        if not roles
        else ("unhealthy" if "unhealthy" in states else ("degraded" if "degraded" in states else "healthy"))
    )
    return _probe_res(True, "ok", overall, overall, roles)


def build_sentinel_wake_envelope(
    role: str, *, reasons: Sequence[str], epoch: int = 1, decision_authority: str = "rd", now: float | None = None
) -> dict[str, Any]:
    """Build and seal a Brick sentinel ``role_unhealthy`` wake envelope (#3295 S6)."""
    norm, ep, authority = _notify.normalize_role(role), int(epoch), str(decision_authority or "").strip()
    if ep < 1:
        raise ValueError("epoch must be >= 1")
    if authority not in VALID_AUTHORITIES:
        raise ValueError(f"decision_authority must be one of {sorted(VALID_AUTHORITIES)}")
    reason_list = [str(r).strip() for r in reasons if str(r).strip()] or ["unknown_health"]
    reason_str = ", ".join(reason_list)
    body = f"Brick sentinel confirmed {norm} unhealthy at epoch {ep} (authority={authority}, reasons={reason_str})."
    return _envelope.seal(
        {
            "kind": "alert",
            "code": "role_unhealthy",
            "role": norm,
            "sender_role": norm,
            "epoch": ep,
            "reasons": reason_list,
            "decision_authority": authority,
            "from": norm,
            "to": "barb",
            "urgency": "high",
            "importance": "high",
            "summary": f"Sentinel wake: persona role {norm} unhealthy ({reason_str})",
            "body": body,
            "recommended_action": f"Wake dormant Grok fallback for persona:{norm} at epoch {ep + 1}.",
            "dedupe_key": f"sentinel-role-unhealthy:{norm}:{ep}:{authority}:{','.join(sorted(reason_list))}",
        },
        _check_ts(now),
    )


def dispatch_wake_envelopes(
    envelopes: Sequence[Mapping[str, Any]],
    *,
    config: Mapping[str, str] | None = None,
    notify_state_path: Path | None = None,
    sender: _notify.Sender = _notify.http_send,
) -> list[dict[str, Any]]:
    """Deliver sentinel wake envelopes through ``persona_notify`` with per-role fault isolation."""
    outcomes: list[dict[str, Any]] = []
    for env in envelopes:
        role = _notify.normalize_role(str(env.get("role") or env.get("sender_role") or "rd-barb"))
        st = notify_state_path.with_name(f"{role}-{notify_state_path.name}") if notify_state_path is not None else None
        outcomes.append(_notify.persona_notify(role, dict(env), config=config, state_path=st, sender=sender))
    return outcomes


def _latch_unhealthy(
    role: str, entry: dict[str, Any], authority: str, clock: float
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    entry["sentinel_state"] = "unhealthy"
    if entry.get("alerted_unhealthy"):
        return entry, None
    entry["alerted_unhealthy"] = True
    return entry, build_sentinel_wake_envelope(
        role, reasons=entry["reasons"], epoch=entry["epoch"], decision_authority=authority, now=clock
    )


class SentinelRoleHealthMonitor:
    """Out-of-band Brick sentinel state machine for per-role health (#3295 S6)."""

    def __init__(self, state_path: Path | None = None) -> None:
        self._path = default_sentinel_role_health_path() if state_path is None else Path(state_path)
        self._lock = threading.Lock()

    def is_backstop_due(self, *, now: float | None = None) -> bool:
        clock = _check_ts(now)
        with self._lock:
            last = self._read().get("last_backstop_at")
            return not isinstance(last, (int, float)) or clock - float(last) >= BACKSTOP_INTERVAL_S

    def record_backstop_check(self, *, now: float | None = None) -> dict[str, Any]:
        clock = _check_ts(now)
        with self._lock:
            state = self._read()
            state["last_backstop_at"] = clock
            self._write(state)
            return {"last_backstop_at": clock, "next_backstop_at": clock + BACKSTOP_INTERVAL_S}

    def evaluate_probe_result(
        self, probe: Mapping[str, Any], *, tracked_roles: Sequence[str], now: float | None = None
    ) -> dict[str, Any]:
        clock = _check_ts(now)
        roles_norm = [_notify.normalize_role(r) for r in tracked_roles]
        if not roles_norm:
            raise ValueError("tracked_roles must contain at least one role")
        reachable = bool(probe.get("reachable"))
        probe_roles: Mapping[str, Any] = r if isinstance(r := probe.get("roles"), Mapping) else {}

        with self._lock:
            state = self._read()
            stored: dict[str, Any] = state.setdefault("roles", {})
            envelopes: list[dict[str, Any]] = []
            snapshots: dict[str, dict[str, Any]] = {}
            for role in roles_norm:
                entry = dict(stored.get(role) or {"role": role, "epoch": 1, "sentinel_state": "degraded"})
                entry, env = (
                    self._eval_unreachable(role, entry, clock)
                    if not reachable
                    else self._eval_reachable(role, entry, probe_roles.get(role), clock)
                )
                stored[role] = entry
                snapshots[role] = dict(entry)
                if env is not None:
                    envelopes.append(env)
            self._write(state)

        states = {snap["sentinel_state"] for snap in snapshots.values()}
        overall = "unhealthy" if "unhealthy" in states else ("degraded" if "degraded" in states else "healthy")
        return {"status": overall, "roles": snapshots, "envelopes": envelopes, "checked_at": clock}

    @staticmethod
    def _eval_unreachable(
        role: str, entry: dict[str, Any], clock: float
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        probes = [float(t) for t in (entry.get("failed_probe_times") or []) if isinstance(t, (int, float))]
        if not probes or clock - probes[-1] >= PROBE_MIN_SEPARATION_S:
            probes.append(clock)
        epoch = max(1, int(entry.get("epoch") or 1))
        entry.update(
            failed_probe_times=probes,
            consecutive_healthy=0,
            reasons=["hub_unreachable"],
            decision_authority="grok",
            epoch=epoch,
        )
        if len(probes) >= HYSTERESIS_READINGS:
            return _latch_unhealthy(role, entry, "grok", clock)
        if entry.get("sentinel_state") != "unhealthy":
            entry["sentinel_state"] = "degraded"
        return entry, None

    @staticmethod
    def _eval_reachable(
        role: str, entry: dict[str, Any], role_report: Any, clock: float
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        entry["failed_probe_times"] = []
        report = role_report if isinstance(role_report, Mapping) else {}
        reported_state = str(report.get("state") or "degraded")
        if reported_state not in VALID_ROLE_STATES:
            reported_state = "degraded"
        entry["epoch"] = max(int(entry.get("epoch") or 1), int(report.get("epoch") or 1))
        raw_reasons = report.get("reasons")
        reasons = (
            [str(r).strip() for r in raw_reasons if str(r).strip()]
            if isinstance(raw_reasons, Sequence) and not isinstance(raw_reasons, (str, bytes))
            else []
        )
        if reported_state == "unhealthy":
            unhealthy_count = int(entry.get("consecutive_unhealthy") or 0) + 1
            entry.update(
                consecutive_unhealthy=unhealthy_count,
                consecutive_healthy=0,
                reasons=reasons or ["unknown_health"],
                decision_authority="rd",
            )
            if unhealthy_count >= HYSTERESIS_READINGS:
                return _latch_unhealthy(role, entry, "rd", clock)
            if entry.get("sentinel_state") != "unhealthy":
                entry["sentinel_state"] = "degraded"
            return entry, None

        if reported_state == "healthy":
            healthy_count = int(entry.get("consecutive_healthy") or 0) + 1
            entry.update(consecutive_healthy=healthy_count, consecutive_unhealthy=0)
            if healthy_count >= HYSTERESIS_READINGS:
                entry.update(sentinel_state="healthy", reasons=[], alerted_unhealthy=False)
            elif entry.get("sentinel_state") != "unhealthy":
                entry.update(sentinel_state="healthy", reasons=[])
            return entry, None

        entry.update(consecutive_unhealthy=0, consecutive_healthy=0, reasons=reasons or ["unknown_health"])
        if entry.get("sentinel_state") != "unhealthy":
            entry["sentinel_state"] = "degraded"
        return entry, None

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"roles": {}}
        except (OSError, ValueError) as exc:
            log.warning("sentinel role health state unreadable (%s); resetting to unknown", type(exc).__name__)
            return {"roles": {}}
        return raw if isinstance(raw, dict) and isinstance(raw.get("roles"), dict) else {"roles": {}}

    def _write(self, state: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)
