"""Contract tests for Private Mode PM-21 supervised dry run, runbook, and enable checklist (#3272)."""

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

for _mod in ("fastapi", "httpx"):
    try:
        __import__(_mod)
    except ModuleNotFoundError:
        _stub = types.ModuleType(_mod)
        _stub.HTTPException = Exception  # type: ignore[attr-defined]
        sys.modules[_mod] = _stub

import private_mode as pm  # noqa: E402
import private_mode_dry_run as pmd  # noqa: E402
from private_mode_emit import assert_no_canary_leak  # noqa: E402


def test_dry_run_checklist_passes_end_to_end_and_requires_dieter_signoff(tmp_path: Path) -> None:
    """Acceptance criterion 1: the dry-run checklist passes end to end; Dieter signs off before enabling."""
    canary = "ExampleCorpDryRunSecretCanary2026"
    t0 = datetime(2026, 10, 28, 23, 30, tzinfo=UTC)
    cfg = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)

    unsigned = pmd.run_supervised_dry_run(
        work_dir=tmp_path / "unsigned", canary=canary, config=cfg, dieter_signoff=False, now=t0
    )
    assert unsigned.passed is True and tuple(unsigned.steps) == pmd.DRY_RUN_CHECKLIST_STEPS
    for step_name, step_res in unsigned.steps.items():
        assert step_res.passed is True, step_name
        assert_no_canary_leak(step_res.metadata, canary, sink_name=f"dry_run:{step_name}")

    assert unsigned.operator_thread_view.metadata_only is True
    assert unsigned.operator_thread_view.badge.level is pm.Level.SENSITIVE
    assert unsigned.operator_audit_view.chain_ok is True and unsigned.operator_audit_view.checked_count >= 1
    assert_no_canary_leak(unsigned.operator_thread_view.to_dict(), canary, sink_name="operator_thread")
    assert_no_canary_leak(unsigned.operator_audit_view.to_dict(), canary, sink_name="operator_audit")
    assert unsigned.enable_checklist.all_technical_gates_passed is True
    assert unsigned.enable_checklist.dieter_signoff is False and unsigned.enable_checklist.ready_to_enable is False

    signed = pmd.run_supervised_dry_run(
        work_dir=tmp_path / "signed", canary=canary, config=cfg, dieter_signoff=True, now=t0
    )
    assert signed.passed is True and signed.enable_checklist.ready_to_enable is True
    assert tuple(signed.enable_checklist.items) == pmd.ENABLE_CHECKLIST_ITEMS
    assert all(signed.enable_checklist.items.values())

    runbook = pmd.get_dry_run_runbook()
    assert tuple(runbook) == pmd.DRY_RUN_CHECKLIST_STEPS
    assert all(isinstance(v, str) and "Example Corp" in v for v in runbook.values())


def test_rollback_flag_off_verified(tmp_path: Path) -> None:
    """Acceptance criterion 2: rollback (flag off) verified."""
    rollback = pmd.verify_flag_off_rollback(
        work_dir=tmp_path / "rollback",
        canary="ExampleCorpRollbackCanary99",
        now=datetime(2026, 10, 28, 23, 35, tzinfo=UTC),
    )
    assert rollback.verified is True and rollback.flag_off_level is pm.Level.PUBLIC
    assert not (rollback.key_provider_enabled or rollback.transcript_store_enabled or rollback.shared_surface_withheld)
    assert rollback.required_check_state == "disabled" and rollback.parked_sensitive_count == 0


def test_flag_off_behaviour_unchanged_and_dbc_preconditions(tmp_path: Path) -> None:
    """Acceptance criterion 3: Flag-off behaviour unchanged (test) + DbC preconditions."""
    report_off = pmd.run_supervised_dry_run(
        work_dir=tmp_path / "off",
        canary="ExampleCorpFlagOffCanary77",
        config=pm.DISABLED,
        dieter_signoff=True,
        now=datetime(2026, 10, 28, 23, 40, tzinfo=UTC),
    )
    assert report_off.config == pm.DISABLED and report_off.passed is True and report_off.rollback.verified is True
    assert report_off.operator_thread_view.metadata_only is False
    assert report_off.operator_thread_view.badge.level is pm.Level.PUBLIC
    assert report_off.enable_checklist.ready_to_enable is False
    assert pm.DRY_RUN_CHECKLIST_STEPS == pmd.DRY_RUN_CHECKLIST_STEPS

    with pytest.raises(pm.ClassificationError, match="canary"):
        pmd.run_supervised_dry_run(work_dir=tmp_path, canary="short")
    with pytest.raises(pm.ClassificationError, match="config"):
        pmd.run_supervised_dry_run(work_dir=tmp_path, canary="ExampleCorpValidCanary", config="bad")  # type: ignore[arg-type]
    with pytest.raises(pm.ClassificationError, match="dieter_signoff"):
        pmd.run_supervised_dry_run(work_dir=tmp_path, canary="ExampleCorpValidCanary", dieter_signoff="yes")  # type: ignore[arg-type]


def test_dry_run_passes_publish_guard() -> None:
    """Acceptance criterion 4: Passes the #3080 publish guard; placeholder fixtures only ('Example Corp')."""
    sys.path.insert(0, str(_ROOT / "scripts"))
    import publish_guard as pg  # noqa: PLC0415

    paths = ["backend/private_mode_dry_run.py", "tests/test_private_mode_dry_run.py"]
    assert pg.check(_ROOT, paths, pg.load_rules()) == []
