"""#1332: ``host.vhdx_compact`` is an owner-approved *request* that runs nothing.

Owner decision (2026-09-25): WSL disks go sparse and fstrim returns freed space; the dashboard
never orchestrates a compaction. Approving a compaction proposal records the request and points
at the manual runbook. Before this fix the alias ran ``maintenance.vacuum_sqlite``, so an
approved "compact the disk" silently vacuumed a SQLite database instead.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from identity import Principal
from staff.actions import ACTION_REGISTRY, ActionContext, ActionRiskClass, execute_proposal
from staff.audit import reset_audit_store
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.maintenance import execute_maintenance, reset_maintenance_cooldowns, verify_maintenance
from staff.maintenance_policy import MaintenancePreconditionError

RUNBOOK = "docs/runbooks/wsl-vhdx-compaction.md"
REQUEST = "maintenance.vhdx_compaction_request"
OWNER = Principal(
    id="test-owner",
    type="human",
    name="Test Owner",
    roles=["owner", "admin"],
    scopes=["staff.read", "staff.approve", "admin", "owner"],
)
APPROVER = Principal(
    id="test-approver",
    type="human",
    name="Test Approver",
    roles=["operator"],
    scopes=["staff.read", "staff.approve", "fleet.maintain"],
)
CTX = ActionContext(thread_id="th-vhdx", caller=OWNER)


@pytest.fixture(autouse=True)
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_vhdx.sqlite3"))
    monkeypatch.setenv("STAFF_RUNS_DIR", str(tmp_path))
    reset_conversation_store()
    reset_audit_store()
    reset_maintenance_cooldowns()
    yield
    reset_conversation_store()
    reset_audit_store()
    reset_maintenance_cooldowns()


def _propose(params: dict[str, str]) -> str:
    store = get_conversation_store()
    thread = store.create_thread(title="Disk", kind="direct", participants=["barb", "user"])
    prop = store.create_proposal(
        message_id="msg-vhdx",
        thread_id=thread.id,
        action="host.vhdx_compact",
        params={**params, "proposing_role": "barb"},
        risk=ActionRiskClass.OWNER_ONLY,
        principal="barb",
    )
    return prop.id


def test_alias_is_the_owner_only_compaction_request_not_a_sqlite_vacuum() -> None:
    action = ACTION_REGISTRY.get("host.vhdx_compact")
    assert action is not None
    assert action.risk_class == ActionRiskClass.OWNER_ONLY
    assert action.required_scope == "fleet.maintain"
    assert "vacuum" not in action.description.lower()


def test_request_records_and_runs_nothing() -> None:
    with (
        patch("staff.maintenance._vacuum_db") as vacuum,
        patch("staff.maintenance._run_service_command") as svc,
        patch("subprocess.run") as run,
    ):
        res = execute_maintenance(REQUEST, {"host": "desk", "reason": "C: at 92%"}, CTX)
    vacuum.assert_not_called()
    svc.assert_not_called()
    run.assert_not_called()
    assert res.success is True
    assert res.result["status"] == "compaction_requested"
    assert res.result["host"] == "desk"
    assert res.result["runbook"] == RUNBOOK
    assert res.result["executed"] is False


def test_request_verifies_as_recorded_not_as_compacted() -> None:
    params = {"host": "desk"}
    res = execute_maintenance(REQUEST, params, CTX)
    ok, detail = verify_maintenance(res, params, CTX, REQUEST)
    assert ok is True
    assert "manual" in detail.lower()
    assert "desk" in detail


@pytest.mark.parametrize("host", ["all", "*", "fleet"])
def test_request_names_exactly_one_host(host: str) -> None:
    with pytest.raises(MaintenancePreconditionError, match="One host at a time"):
        execute_maintenance(REQUEST, {"host": host}, CTX)


def test_request_without_a_host_is_refused() -> None:
    with pytest.raises(MaintenancePreconditionError, match="requires an explicit host"):
        execute_maintenance(REQUEST, {}, CTX)


def test_a_non_owner_cannot_approve_the_request() -> None:
    prop_id = _propose({"host": "desk"})
    with pytest.raises(PermissionError, match="requires owner approval"):
        execute_proposal(prop_id, approver=APPROVER, approve=True)
    prop = get_conversation_store().get_proposal(prop_id)
    assert prop is not None and prop.state == "proposed"


def test_owner_approval_records_the_request() -> None:
    prop_id = _propose({"host": "desk"})
    with patch("staff.maintenance._vacuum_db") as vacuum:
        res = execute_proposal(prop_id, approver=OWNER, approve=True)
    vacuum.assert_not_called()
    assert res.success is True
    assert res.result["status"] == "compaction_requested"
