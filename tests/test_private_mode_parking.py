"""Contract tests for Private Mode PM-11 bounded parking, EXPIRED state, metrics, and alerts (issue #3262)."""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

try:
    import fastapi  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("fastapi")
    _stub.HTTPException = Exception  # type: ignore[attr-defined]
    sys.modules["fastapi"] = _stub

import private_mode as pm  # noqa: E402
import private_mode_admission as pma  # noqa: E402
import private_mode_parking as pmp  # noqa: E402
from private_context_audit import PrivateContextAuditStore  # noqa: E402
from private_mode_emit import assert_no_canary_leak, build_canary_variants  # noqa: E402

_CFG = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)


def _sample_bounds() -> pmp.ParkingBounds:
    return pmp.parse_parking_bounds(
        {
            "max_depth_per_level": {"PUBLIC": 4, "INTERNAL": 2, "SENSITIVE": 2},
            "max_age_seconds": 600,
            "escalation_owner": "sec-oncall",
        }
    )


def _parked_decision(
    job_id: str,
    level: pm.Level = pm.Level.SENSITIVE,
    *,
    state: pma.AdmissionState = pma.AdmissionState.PARKED_CAPACITY,
    reason: str = "lane-capacity-exhausted",
    owner: str = "pm-rd",
    deadline: str = "2026-10-28T21:10:00Z",
) -> pma.AdmissionDecision:
    return pma.AdmissionDecision(job_id, state, level, "local:claude", reason, owner, deadline)


def test_depth_and_age_bounds_enforced_with_metrics(tmp_path: Path) -> None:
    """Acceptance criterion 1: depth and age bounds enforced; depth/age metrics reported accurately."""
    audit = PrivateContextAuditStore(tmp_path / "audit.db")
    alerts: list[dict[str, Any]] = []
    queue = pmp.BoundedParkingQueue(bounds=_sample_bounds(), config=_CFG, audit_store=audit, alert_sink=alerts.append)
    t0 = datetime(2026, 10, 28, 21, 0, tzinfo=UTC)

    rec1 = queue.enqueue(_parked_decision("job-sens-1"), repo="example-corp/widgets-sensitive", now=t0)
    rec2 = queue.enqueue(
        _parked_decision("job-sens-2", deadline="2026-10-28T21:12:00Z"),
        repo="example-corp/widgets-sensitive",
        now=t0 + timedelta(seconds=120),
    )
    assert rec1.state is pmp.ParkingState.PARKED_CAPACITY and rec2.state is pmp.ParkingState.PARKED_CAPACITY

    metrics_mid = queue.metrics(now=t0 + timedelta(seconds=300))
    assert metrics_mid.depth_by_level[pm.Level.SENSITIVE] == 2
    assert metrics_mid.oldest_age_seconds_by_level[pm.Level.SENSITIVE] == pytest.approx(300.0)
    assert metrics_mid.expired_count_by_level[pm.Level.SENSITIVE] == 0

    rec_overflow = queue.enqueue(
        _parked_decision("job-sens-3"), repo="example-corp/widgets-sensitive", now=t0 + timedelta(seconds=300)
    )
    assert rec_overflow.state is pmp.ParkingState.EXPIRED and rec_overflow.reason == "parking-depth-exceeded"
    assert rec_overflow.escalated_to == "sec-oncall"

    metrics_ovf = queue.metrics(now=t0 + timedelta(seconds=300))
    assert metrics_ovf.depth_by_level[pm.Level.SENSITIVE] == 2
    assert metrics_ovf.overflow_count_by_level[pm.Level.SENSITIVE] == 1
    assert metrics_ovf.expired_count_by_level[pm.Level.SENSITIVE] == 1

    expired = queue.sweep_expired(now=t0 + timedelta(seconds=601))
    assert [r.job_id for r in expired] == ["job-sens-1"]
    assert expired[0].state is pmp.ParkingState.EXPIRED and expired[0].reason == "parking-age-expired"
    assert expired[0].escalated_to == "sec-oncall"

    metrics_sweep = queue.metrics(now=t0 + timedelta(seconds=601))
    assert metrics_sweep.depth_by_level[pm.Level.SENSITIVE] == 1
    assert metrics_sweep.oldest_age_seconds_by_level[pm.Level.SENSITIVE] == pytest.approx(481.0)
    assert metrics_sweep.expired_count_by_level[pm.Level.SENSITIVE] == 2


def test_expiry_visible_and_audited(tmp_path: Path) -> None:
    """Acceptance criterion 2: expiry is visible in queue state and recorded in the audit trail."""
    audit = PrivateContextAuditStore(tmp_path / "audit.db")
    queue = pmp.BoundedParkingQueue(bounds=_sample_bounds(), config=_CFG, audit_store=audit)
    t0 = datetime(2026, 10, 28, 21, 0, tzinfo=UTC)

    queue.enqueue(_parked_decision("job-exp-1"), repo="example-corp/widgets-sensitive", now=t0)
    expired = queue.sweep_expired(now=t0 + timedelta(seconds=700))
    assert len(expired) == 1

    visible = queue.get_record("job-exp-1")
    assert visible is not None and visible.state is pmp.ParkingState.EXPIRED
    assert visible.expired_at == "2026-10-28T21:11:40Z" and visible.escalated_to == "sec-oncall"
    assert [r.job_id for r in queue.list_expired(pm.Level.SENSITIVE)] == ["job-exp-1"]
    assert queue.list_parked(pm.Level.SENSITIVE) == ()

    expired_events = [e for e in audit.list_decisions() if e.action == "parking_expired"]
    assert len(expired_events) == 1 and expired_events[0].outcome == "EXPIRED"
    assert expired_events[0].actor == "sec-oncall" and expired_events[0].metadata["reason"] == "parking-age-expired"


def test_alert_payload_goes_via_emit_shared_and_passes_canary(tmp_path: Path) -> None:
    """Acceptance criterion 3: alert payload goes via emit_shared and passes all PM-5 canary forms."""
    canary = "ExampleCorpCanarySecret9876"
    variants = build_canary_variants(canary)
    alerts: list[dict[str, Any]] = []
    audit = PrivateContextAuditStore(tmp_path / "audit.db")
    queue = pmp.BoundedParkingQueue(bounds=_sample_bounds(), config=_CFG, audit_store=audit, alert_sink=alerts.append)
    t0 = datetime(2026, 10, 28, 21, 0, tzinfo=UTC)

    dec = _parked_decision(f"job-{variants.raw}", pm.Level.SENSITIVE)
    queue.enqueue(
        dec,
        repo=f"example-corp/{variants.url_encoded}",
        now=t0,
        extra_alert_fields={"prompt": variants.raw, "b64": variants.base64, "split": variants.split[0]},
    )
    queue.sweep_expired(
        now=t0 + timedelta(seconds=650),
        extra_alert_fields={
            "summary": f"raw={variants.raw} b64={variants.base64} url={variants.url_encoded}",
            "split_hint": f"{variants.split[0]}::{variants.split[1]}",
        },
    )
    assert len(alerts) == 1
    alert_payload = alerts[0]
    assert alert_payload["surface"] == "alerts" and alert_payload["level"] == "SENSITIVE"
    assert alert_payload["withheld"] is True and "content" not in alert_payload
    assert alert_payload["metadata"]["state"] == "expired" and alert_payload["metadata"]["degraded"] is True
    assert_no_canary_leak(alert_payload, canary, sink_name="alerts")


def test_flag_off_behaviour_unchanged_and_dbc_preconditions() -> None:
    """Acceptance criterion 4: Flag-off behaviour unchanged; DbC preconditions enforced."""
    alerts: list[dict[str, Any]] = []
    queue_off = pmp.BoundedParkingQueue(bounds=_sample_bounds(), config=pm.DISABLED, alert_sink=alerts.append)
    t0 = datetime(2026, 10, 28, 21, 0, tzinfo=UTC)
    rec = queue_off.enqueue(_parked_decision("job-off-1"), repo="example-corp/widgets", now=t0)
    assert rec.level is pm.Level.PUBLIC and queue_off.sweep_expired(now=t0 + timedelta(seconds=9999)) == ()
    assert alerts == []

    bad_depths = {"PUBLIC": 1, "INTERNAL": 1, "SENSITIVE": 1}
    with pytest.raises(pm.ClassificationError, match="max_age_seconds"):
        pmp.parse_parking_bounds({"max_depth_per_level": bad_depths, "max_age_seconds": 0, "escalation_owner": "ops"})
    ready_dec = pma.AdmissionDecision("job-ready", pma.AdmissionState.READY, pm.Level.SENSITIVE, "local:claude")
    queue_on = pmp.BoundedParkingQueue(bounds=_sample_bounds(), config=_CFG)
    with pytest.raises(pm.ClassificationError, match="parked"):
        queue_on.enqueue(ready_dec, repo="example-corp/widgets", now=t0)


def test_parking_files_pass_publish_guard() -> None:
    """Acceptance criterion 5: Passes the #3080 publish guard; placeholder fixtures only ('Example Corp')."""
    sys.path.insert(0, str(_ROOT / "scripts"))
    import publish_guard as pg  # noqa: PLC0415

    paths = ["backend/private_mode_parking.py", "tests/test_private_mode_parking.py"]
    assert pg.check(_ROOT, paths, pg.load_rules()) == []
