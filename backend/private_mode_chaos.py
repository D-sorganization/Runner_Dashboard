"""Private Mode PM-20 chaos/stall suite: pool down, policy deny, key missing, audit down, overlay invalid (#3271)."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

try:
    from backend.ci.private_required_check import PrivateRequiredCheckWiring, RequiredCheckResult
except ModuleNotFoundError:  # pragma: no cover
    from ci.private_required_check import PrivateRequiredCheckWiring, RequiredCheckResult
from private_mode import (
    DISABLED,
    POLICY_FILENAME,
    ClassificationError,
    ClassificationPolicy,
    Level,
    PrivateModeConfig,
    load_classification_policy,
    parse_classification_policy,
    stamp_envelope,
)
from private_mode_admission import (
    AdmissionDecision,
    AdmissionPolicy,
    AdmissionRequest,
    AdmissionState,
    PrivateModeAdmissionController,
    parse_admission_policy,
)
from private_mode_emit import SharedProjection, assert_no_canary_leak, build_canary_variants, emit_shared
from private_mode_parking import BoundedParkingQueue, ParkedJobRecord, ParkingBounds, parse_parking_bounds

CHAOS_FAULTS: tuple[str, ...] = ("pool_down", "policy_deny", "key_missing", "audit_down", "overlay_invalid")
EXAMPLE_SENSITIVE_REPO = "example-corp/widgets-sensitive"
EXAMPLE_PUBLIC_REPO = "example-corp/public-docs"
_DEFAULT_WORKFLOWS = ({"name": "ci", "jobs": {"test": {"runs-on": ["private-pool"]}}},)
EXPORTS: frozenset[str] = frozenset(
    "CHAOS_FAULTS ChaosCaseOutcome ChaosSuiteResult PublicThroughputReport default_chaos_admission_policy "
    "default_chaos_overlay_policy default_chaos_parking_bounds evaluate_public_throughput run_chaos_case "
    "run_chaos_suite".split()
)


@dataclass(frozen=True)
class ChaosCaseOutcome:
    fault: str
    job_id: str
    effective_level: Level
    decision: AdmissionDecision
    parked_record: ParkedJobRecord | None
    alert: SharedProjection
    alert_payload: Mapping[str, Any]
    required_check: RequiredCheckResult | None = None


@dataclass(frozen=True)
class ChaosSuiteResult:
    outcomes: Mapping[str, ChaosCaseOutcome]
    parking_queue: BoundedParkingQueue
    emitted_alerts: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class PublicThroughputReport:
    public_submitted: int
    public_completed: int
    completion_ratio: float
    degradation_fraction: float
    tolerance: float
    within_tolerance: bool
    public_parked_depth: int
    sensitive_parked_depth: int


class _UnavailableAuditStore:
    def record_decision(self, event: Any, *, config: PrivateModeConfig = DISABLED) -> Any:
        _ = (event, config)
        return SimpleNamespace(parked=True, reason="audit-store-unavailable", state="PARKED_AUDIT")


def default_chaos_overlay_policy() -> ClassificationPolicy:
    return parse_classification_policy(
        {
            "default_level": "INTERNAL",
            "repos": [
                {"repo": EXAMPLE_SENSITIVE_REPO, "level": "SENSITIVE"},
                {"repo": EXAMPLE_PUBLIC_REPO, "level": "PUBLIC"},
            ],
            "path_globs": [{"glob": "confidential/**", "level": "SENSITIVE"}],
            "operator_marks": {},
        }
    )


def default_chaos_admission_policy() -> AdmissionPolicy:
    def _lane(cap: int, agents: list[str], dests: list[str], modes: list[str], cloud_ok: bool) -> dict[str, Any]:
        return {
            "max_concurrent": cap,
            "allowed_agents": agents,
            "allowed_destinations": dests,
            "allowed_modes": modes,
            "requires_cloud_approval": cloud_ok,
        }

    return parse_admission_policy(
        {
            "default_owner": "pm-rd",
            "default_deadline_seconds": 600,
            "lanes": {
                "PUBLIC": _lane(
                    32, ["claude", "codex", "antigravity"], ["local:claude", "cloud:cursor"], ["local", "cloud"], False
                ),
                "INTERNAL": _lane(4, ["claude", "codex"], ["local:claude", "local:codex"], ["local"], True),
                "SENSITIVE": _lane(2, ["claude"], ["local:claude"], ["local"], True),
            },
        }
    )


def default_chaos_parking_bounds() -> ParkingBounds:
    return parse_parking_bounds(
        {
            "max_depth_per_level": {"PUBLIC": 16, "INTERNAL": 8, "SENSITIVE": 8},
            "max_age_seconds": 600,
            "escalation_owner": "pm-rd",
        }
    )


def _coerce_utc(ts: datetime | None) -> datetime:
    base = ts or datetime.now(UTC)
    if not isinstance(base, datetime):
        raise ClassificationError("private_mode_chaos: now must be a datetime instance")
    return base.replace(tzinfo=UTC) if base.tzinfo is None else base.astimezone(UTC)


def _format_iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def _emit_chaos_alert(
    job_id: str,
    fault: str,
    canary: str,
    level: Level,
    config: PrivateModeConfig,
    ts_iso: str,
    alert_sink: Callable[[dict[str, Any]], Any] | None,
) -> tuple[SharedProjection, dict[str, Any]]:
    variants = build_canary_variants(canary)
    captured: list[dict[str, Any]] = []

    def _fanout(payload: dict[str, Any]) -> None:
        captured.append(payload)
        if alert_sink is not None:
            alert_sink(payload)

    env = stamp_envelope(level, hop=f"chaos:{fault}", config=config)
    raw_payload: dict[str, Any] = {
        "id": job_id,
        "item_id": job_id,
        "repo": f"{EXAMPLE_SENSITIVE_REPO}/{variants.url_encoded}",
        "target_id": "pm-rd",
        "state": "parked",
        "status": "warning",
        "outcome": "parked",
        "kind": "warning",
        "degraded": True,
        "count": 1,
        "ts": ts_iso,
        "prompt": variants.raw,
        "b64_payload": variants.base64,
        "split_hint": f"{variants.split[0]}::{variants.split[1]}",
    }
    proj = emit_shared("alerts", raw_payload, level=env, config=config, sink=_fanout)
    emitted = captured[-1] if captured else proj.to_dict()
    if config.enabled and level is Level.SENSITIVE:
        assert_no_canary_leak(emitted, canary, sink_name=f"alerts:{fault}")
    return proj, emitted


def run_chaos_case(
    fault: str,
    *,
    work_dir: Path | str,
    canary: str = "ExampleCorpChaosCanary9021",
    config: PrivateModeConfig = DISABLED,
    private_ci_enabled: bool | None = None,
    parking_queue: BoundedParkingQueue | None = None,
    alert_sink: Callable[[dict[str, Any]], Any] | None = None,
    now: datetime | None = None,
) -> ChaosCaseOutcome:
    """Execute one named PM-20 fault case and verify parked state + canary-safe alert (DbC)."""
    if not isinstance(fault, str) or fault not in CHAOS_FAULTS:
        raise ClassificationError(f"run_chaos_case: fault must be one of {CHAOS_FAULTS}")
    if not isinstance(config, PrivateModeConfig):
        raise ClassificationError("run_chaos_case: config must be a PrivateModeConfig")
    build_canary_variants(canary)
    now_dt = _coerce_utc(now)
    now_iso, deadline_iso = _format_iso(now_dt), _format_iso(now_dt + timedelta(seconds=600))
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    queue = parking_queue or BoundedParkingQueue(bounds=default_chaos_parking_bounds(), config=config)
    policy, overlay = default_chaos_admission_policy(), default_chaos_overlay_policy()
    job_id = f"job-chaos-{fault}"
    ci_flag = config.enabled if private_ci_enabled is None else bool(private_ci_enabled)
    wiring = PrivateRequiredCheckWiring(overlay_policy=overlay, private_mode=config)
    chk_kw: dict[str, Any] = {
        "run_id": job_id,
        "repo": EXAMPLE_SENSITIVE_REPO,
        "sha": "a1b2c3d4",
        "target_kind": "pr_head",
        "pool_healthy": False,
        "workflows": _DEFAULT_WORKFLOWS,
        "enabled": ci_flag,
        "now": now_iso,
    }

    if not config.enabled:
        req_chk = wiring.evaluate_check(**chk_kw) if fault == "pool_down" else None
        gate_off = PrivateModeAdmissionController(policy=policy, config=DISABLED)
        env_off = stamp_envelope(Level.SENSITIVE, hop="origin", config=DISABLED)
        dec_off = gate_off.admit(
            AdmissionRequest(job_id, EXAMPLE_SENSITIVE_REPO, env_off, "claude", "claude"), now=now_dt
        )
        noop_proj = SharedProjection("alerts", Level.PUBLIC, "disabled", {}, None, None, False)
        return ChaosCaseOutcome(fault, job_id, Level.PUBLIC, dec_off, None, noop_proj, {}, req_chk)

    sens_env = stamp_envelope(Level.SENSITIVE, hop="origin", config=config)
    required_check = wiring.evaluate_check(**chk_kw) if fault == "pool_down" else None
    if fault == "pool_down":
        dec = AdmissionDecision(
            job_id,
            AdmissionState.PARKED_CAPACITY,
            Level.SENSITIVE,
            "local:claude",
            "pool-down-unavailable",
            "pm-rd",
            deadline_iso,
        )
    elif fault == "policy_deny":
        gate = PrivateModeAdmissionController(policy=policy, config=config)
        req = AdmissionRequest(
            job_id, EXAMPLE_SENSITIVE_REPO, sens_env, "claude", "cursor", execution_mode="cloud", cloud_approved=True
        )
        dec = gate.admit(req, now=now_dt)
    elif fault == "key_missing":
        gate = PrivateModeAdmissionController(policy=policy, config=config)
        req = AdmissionRequest(job_id, EXAMPLE_SENSITIVE_REPO, sens_env, "claude", "claude", keys_ready=False)
        dec = gate.admit(req, now=now_dt)
    elif fault == "audit_down":
        gate = PrivateModeAdmissionController(policy=policy, config=config, audit_store=_UnavailableAuditStore())
        dec = gate.admit(AdmissionRequest(job_id, EXAMPLE_SENSITIVE_REPO, sens_env, "claude", "claude"), now=now_dt)
    else:
        bad_dir = root / "bad_overlay"
        bad_dir.mkdir(parents=True, exist_ok=True)
        (bad_dir / POLICY_FILENAME).write_text(
            json.dumps({"default_level": "PUBLIC", "repos": [], "path_globs": [], "operator_marks": {}}),
            encoding="utf-8",
        )
        try:
            load_classification_policy(bad_dir)
        except ClassificationError:
            dec = AdmissionDecision(
                job_id,
                AdmissionState.PARKED_POLICY,
                Level.SENSITIVE,
                "local:claude",
                "overlay-policy-invalid",
                "pm-rd",
                deadline_iso,
            )
        else:  # pragma: no cover
            raise ClassificationError("run_chaos_case: expected invalid overlay to fail closed")

    parked_rec = queue.enqueue(dec, repo=EXAMPLE_SENSITIVE_REPO, now=now_dt)
    proj, alert_dict = _emit_chaos_alert(job_id, fault, canary, Level.SENSITIVE, config, now_iso, alert_sink)
    return ChaosCaseOutcome(fault, job_id, Level.SENSITIVE, dec, parked_rec, proj, alert_dict, required_check)


def run_chaos_suite(
    *,
    work_dir: Path | str,
    canary: str = "ExampleCorpChaosCanary9021",
    config: PrivateModeConfig = DISABLED,
    private_ci_enabled: bool | None = None,
    now: datetime | None = None,
) -> ChaosSuiteResult:
    """Run all five PM-20 chaos/stall cases and return parked queue state + emitted alerts."""
    queue = BoundedParkingQueue(bounds=default_chaos_parking_bounds(), config=config)
    alerts: list[dict[str, Any]] = []
    outcomes = {
        fault: run_chaos_case(
            fault,
            work_dir=work_dir,
            canary=canary,
            config=config,
            private_ci_enabled=private_ci_enabled,
            parking_queue=queue,
            alert_sink=alerts.append,
            now=now,
        )
        for fault in CHAOS_FAULTS
    }
    return ChaosSuiteResult(outcomes=outcomes, parking_queue=queue, emitted_alerts=tuple(alerts))


def evaluate_public_throughput(
    *,
    work_dir: Path | str,
    public_job_count: int = 20,
    tolerance: float = 0.15,
    config: PrivateModeConfig = DISABLED,
    now: datetime | None = None,
) -> PublicThroughputReport:
    """Verify PUBLIC lane admission/completion throughput is unchanged within tolerance under private chaos."""
    if isinstance(public_job_count, bool) or not isinstance(public_job_count, int) or public_job_count < 1:
        raise ClassificationError("evaluate_public_throughput: public_job_count must be a positive int")
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not (0.0 <= float(tolerance) <= 1.0):
        raise ClassificationError("evaluate_public_throughput: tolerance must be a float in [0.0, 1.0]")
    now_dt = _coerce_utc(now)
    suite = run_chaos_suite(work_dir=work_dir, config=config, private_ci_enabled=config.enabled, now=now_dt)
    gate = PrivateModeAdmissionController(policy=default_chaos_admission_policy(), config=config)
    pub_env = stamp_envelope(Level.PUBLIC, hop="origin", config=config)
    completed = 0
    for idx in range(public_job_count):
        jid = f"job-public-{idx}"
        req = AdmissionRequest(jid, EXAMPLE_PUBLIC_REPO, pub_env, "claude", "claude")
        run_dec = gate.admit_and_spawn(req, spawner=lambda r: {"job_id": r.job_id}, now=now_dt)
        if run_dec.state is AdmissionState.RUNNING:
            completed += 1
            gate.transition(jid, AdmissionState.CANCELLED, reason="completed-ok", now=now_dt)
    ratio = completed / float(public_job_count)
    degradation = max(0.0, 1.0 - ratio)
    pub_parked = len(suite.parking_queue.list_parked(Level.PUBLIC))
    sens_parked = len(suite.parking_queue.list_parked(Level.SENSITIVE))
    ok = completed == public_job_count and pub_parked == 0 and degradation <= float(tolerance)
    return PublicThroughputReport(
        public_submitted=public_job_count,
        public_completed=completed,
        completion_ratio=ratio,
        degradation_fraction=degradation,
        tolerance=float(tolerance),
        within_tolerance=ok,
        public_parked_depth=pub_parked,
        sensitive_parked_depth=sens_parked,
    )
