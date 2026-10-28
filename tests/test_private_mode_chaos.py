"""Contract tests for Private Mode PM-20 chaos/stall suite (issue #3271, epic #3249)."""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "backend"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:
    import fastapi  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("fastapi")
    _stub.HTTPException = Exception  # type: ignore[attr-defined]
    sys.modules["fastapi"] = _stub

import private_mode as pm  # noqa: E402
import private_mode_chaos as pmc  # noqa: E402
import private_mode_parking as pmp  # noqa: E402
from private_mode_emit import assert_no_canary_leak  # noqa: E402


def test_each_chaos_case_produces_visible_parked_state_and_canary_safe_alert(tmp_path: Path) -> None:
    """Acceptance criterion 1: each case -> visible parked state + alert (reusing PM-5 canary harness)."""
    canary = "ExampleCorpChaosCanary9021"
    t0 = datetime(2026, 10, 28, 23, 0, tzinfo=UTC)
    suite = pmc.run_chaos_suite(
        work_dir=tmp_path,
        canary=canary,
        config=pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL),
        private_ci_enabled=True,
        now=t0,
    )
    assert tuple(suite.outcomes) == pmc.CHAOS_FAULTS
    expected_states = {
        "pool_down": (pmp.ParkingState.PARKED_CAPACITY, "pool-down-unavailable"),
        "policy_deny": (pmp.ParkingState.PARKED_POLICY, "mode-not-allowlisted"),
        "key_missing": (pmp.ParkingState.PARKED_POLICY, "keys-unavailable"),
        "audit_down": (pmp.ParkingState.PARKED_AUDIT, "audit-store-unavailable"),
        "overlay_invalid": (pmp.ParkingState.PARKED_POLICY, "overlay-policy-invalid"),
    }
    for fault, exp_pair in expected_states.items():
        outcome = suite.outcomes[fault]
        rec = outcome.parked_record
        assert rec is not None and rec.state is exp_pair[0] and rec.reason == exp_pair[1]
        assert rec.level is pm.Level.SENSITIVE and rec.owner == "pm-rd"
        assert suite.parking_queue.get_record(rec.job_id) == rec
        assert outcome.alert.surface == "alerts" and outcome.alert.withheld is True
        assert outcome.alert_payload["metadata"]["degraded"] is True
        assert_no_canary_leak(outcome.alert_payload, canary, sink_name=f"alerts:{fault}")

    pool_chk = suite.outcomes["pool_down"].required_check
    assert pool_chk is not None and pool_chk.state == "pending"
    assert pool_chk.auto_passed is False and pool_chk.alert_emitted is True and pool_chk.sla_deadline is not None
    assert len(suite.parking_queue.list_parked(pm.Level.SENSITIVE)) == len(pmc.CHAOS_FAULTS)


def test_public_throughput_unchanged_within_tolerance_under_private_chaos(tmp_path: Path) -> None:
    """Acceptance criterion 2: PUBLIC throughput unchanged within tolerance while private lane stalls."""
    report = pmc.evaluate_public_throughput(
        work_dir=tmp_path,
        public_job_count=20,
        tolerance=0.15,
        config=pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL),
        now=datetime(2026, 10, 28, 23, 5, tzinfo=UTC),
    )
    assert report.public_submitted == 20 and report.public_completed == 20
    assert report.completion_ratio == pytest.approx(1.0)
    assert report.public_parked_depth == 0
    assert report.sensitive_parked_depth == len(pmc.CHAOS_FAULTS)
    assert report.within_tolerance is True and report.degradation_fraction <= 0.15


def test_flag_off_behaviour_unchanged_and_dbc_preconditions(tmp_path: Path) -> None:
    """Acceptance criterion 3: Flag-off behaviour unchanged and DbC preconditions enforced."""
    suite_off = pmc.run_chaos_suite(
        work_dir=tmp_path / "off",
        canary="ExampleCorpFlagOffCanary12",
        config=pm.DISABLED,
        private_ci_enabled=False,
        now=datetime(2026, 10, 28, 23, 10, tzinfo=UTC),
    )
    assert suite_off.emitted_alerts == ()
    assert suite_off.parking_queue.list_parked(pm.Level.SENSITIVE) == ()
    for outcome in suite_off.outcomes.values():
        assert outcome.effective_level is pm.Level.PUBLIC
        assert outcome.parked_record is None
    pool_off = suite_off.outcomes["pool_down"].required_check
    assert pool_off is not None and pool_off.enabled is False and pool_off.state == "disabled"

    assert pm.CHAOS_FAULTS == pmc.CHAOS_FAULTS
    with pytest.raises(pm.ClassificationError, match="fault"):
        pmc.run_chaos_case(
            "unknown_fault",
            work_dir=tmp_path,
            canary="ExampleCorpFlagOffCanary12",
            config=pm.DISABLED,
        )
    with pytest.raises(pm.ClassificationError, match="public_job_count"):
        pmc.evaluate_public_throughput(work_dir=tmp_path, public_job_count=0, tolerance=0.15)
    with pytest.raises(pm.ClassificationError, match="tolerance"):
        pmc.evaluate_public_throughput(work_dir=tmp_path, public_job_count=5, tolerance=1.5)


def test_chaos_suite_passes_publish_guard() -> None:
    """Acceptance criterion 4: Passes the #3080 publish guard; placeholder fixtures only ('Example Corp')."""
    sys.path.insert(0, str(_ROOT / "scripts"))
    import publish_guard as pg  # noqa: PLC0415

    paths = ["backend/private_mode_chaos.py", "tests/test_private_mode_chaos.py"]
    assert pg.check(_ROOT, paths, pg.load_rules()) == []
