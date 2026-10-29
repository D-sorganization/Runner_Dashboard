"""Qualified node-b GitHub inventory diagnostics (#1838)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# deploy/ source files stay untouched until the #2657 deploy hold lifts.
MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "deploy"
    / (
        "oglaptop_github_inventory.py"  # pragma: allowlist private-topology
    )
)
spec = importlib.util.spec_from_file_location("node_b_github_inventory", MODULE_PATH)
assert spec is not None and spec.loader is not None
inventory = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = inventory
spec.loader.exec_module(inventory)

pytestmark = pytest.mark.unit

PREFIX = inventory.CANONICAL_PREFIX


def _runner(slot: int, *, status: str = "online", busy: bool = False, legacy: bool = False) -> dict:
    name_prefix = inventory.LEGACY_PREFIX if legacy else PREFIX
    return {"name": f"{name_prefix}{slot}", "status": status, "busy": busy}


def _pages(*runners: dict, total_count: int | None = None) -> list[dict]:
    count = total_count if total_count is not None else len(runners)
    return [{"total_count": count, "runners": list(runners)}]


def _full_daytime_posture(*, busy_slot_one: bool = True) -> list[dict]:
    runners = []
    for slot in range(1, 9):
        runners.append(
            _runner(
                slot,
                status="online" if slot <= 4 else "offline",
                busy=busy_slot_one if slot == 1 else False,
            )
        )
    return _pages(*runners)


def test_missing_standby_seven_and_eight_reports_slots() -> None:
    runners = [_runner(slot, status="online" if slot <= 4 else "offline") for slot in range(1, 7)]
    with pytest.raises(inventory.OglaptopInventoryError) as excinfo:
        inventory.assert_exact_canonical_inventory(_pages(*runners))
    failure = excinfo.value.failure
    assert failure.missing_slots == (7, 8)
    assert "missing GitHub registrations" in failure.message
    assert inventory.PURGE_RUNBOOK in failure.message


def test_legacy_github_name_is_not_counted_as_canonical() -> None:
    runners = [_runner(slot, status="online" if slot <= 4 else "offline") for slot in range(1, 7)]
    runners.append(_runner(7, status="offline", legacy=True))
    runners.append(_runner(8, status="offline", legacy=True))
    with pytest.raises(inventory.OglaptopInventoryError) as excinfo:
        inventory.assert_exact_canonical_inventory(_pages(*runners))
    failure = excinfo.value.failure
    assert failure.missing_slots == (7, 8)
    assert failure.legacy_github_names == (
        f"{inventory.LEGACY_PREFIX}7",
        f"{inventory.LEGACY_PREFIX}8",
    )


def test_legacy_local_metadata_blocks_qualification(tmp_path: Path) -> None:
    runner_dir = tmp_path / "runner-7"
    runner_dir.mkdir()
    (runner_dir / ".runner").write_text(
        json.dumps({"agentName": f"{inventory.LEGACY_PREFIX}7"}),
        encoding="utf-8",
    )
    legacy = inventory.scan_local_legacy_agent_names(tmp_path)
    assert legacy == (f"{inventory.LEGACY_PREFIX}7",)
    with pytest.raises(inventory.OglaptopInventoryError) as excinfo:
        inventory.assert_exact_canonical_inventory(_full_daytime_posture(), legacy_local=legacy)
    assert "local .runner agentName" in excinfo.value.failure.message


def test_validate_pre_accepts_complete_daytime_posture() -> None:
    inventory.validate_qualified_pre_transaction(
        _full_daytime_posture(),
        f"{PREFIX}1",
    )


def test_evidence_summary_lists_missing_slots() -> None:
    runners = [_runner(slot) for slot in range(1, 7)]
    summary = inventory.evidence_summary(_pages(*runners))
    assert summary["missing_slots"] == [7, 8]
    assert summary["complete"] is True


def test_scan_local_cli_exits_zero_when_canonical(tmp_path: Path, capsys) -> None:
    (tmp_path / "runner-1").mkdir()
    (tmp_path / "runner-1" / ".runner").write_text(
        json.dumps({"agentName": f"{PREFIX}1"}),
        encoding="utf-8",
    )
    assert inventory.main(["scan-local", str(tmp_path)]) == 0


def test_scan_local_cli_exits_one_for_legacy(tmp_path: Path) -> None:
    (tmp_path / "runner-7").mkdir()
    (tmp_path / "runner-7" / ".runner").write_text(
        json.dumps({"agentName": f"{inventory.LEGACY_PREFIX}7"}),
        encoding="utf-8",
    )
    assert inventory.main(["scan-local", str(tmp_path)]) == 1
