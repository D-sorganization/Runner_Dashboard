"""Unit and failure-injection tests for fail-closed ``staff.holds`` (#3541)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import staff.budget as budget_mod
import staff.holds as holds_mod
import staff.roles as roles_mod
import staff.scheduler as scheduler_mod
import staff.store as store_mod
from staff.runner import RunRecord, RunRequest

LA = ZoneInfo("America/Los_Angeles")


class _FakeRunner:
    machine = "TestNode"

    def __init__(self, store: store_mod.RunStore, roles: dict[str, roles_mod.RoleSpec]) -> None:
        self.store = store
        self._roles = roles
        self.submitted: list[RunRequest] = []

    def roles(self) -> dict[str, roles_mod.RoleSpec]:
        return self._roles

    def submit(self, req: RunRequest) -> RunRecord:
        self.submitted.append(req)
        rec = RunRecord(
            id=f"run-{len(self.submitted)}",
            role=req.role,
            provider="fake",
            model="fake-model",
            machine=self.machine,
            repo=req.repo,
            target_kind="repo",
            target_ref=req.repo,
            prompt=req.prompt,
            requested_by=req.requested_by,
        )
        return rec


@pytest.mark.unit
def test_corrupt_json_holds_file_fails_closed_with_error_and_metric(tmp_path: Path) -> None:
    holds_mod.reset_holds_metrics()
    bad_path = tmp_path / "staff_holds.json"
    bad_path.write_text("{not valid json", encoding="utf-8")

    holds = holds_mod.HoldsList(bad_path, roles_loader=dict)
    loaded = holds.load()

    assert len(loaded) == 1
    blocker = holds.blocking("night-watch", "Runner_Dashboard")
    assert blocker is not None
    assert blocker.id == holds_mod.FAIL_CLOSED_HOLD_ID
    assert blocker.kind == "schedule"
    assert blocker.active is True
    assert blocker.applies_to == ["*"]
    assert "failing closed" in blocker.text
    assert bad_path.read_text(encoding="utf-8") == "{not valid json"

    metrics = holds_mod.holds_metrics_snapshot()
    assert metrics["load_errors_total"] >= 1
    assert metrics["corrupt"] == 1

    from routers import metrics as metrics_router  # noqa: PLC0415

    rendered = metrics_router.render_prometheus_text(
        {
            "holds_active": len(holds_mod.active_holds()),
            "holds_load_errors_total": metrics["load_errors_total"],
            "holds_corrupt": metrics["corrupt"],
        }
    )
    assert f"rd_holds_load_errors_total {metrics['load_errors_total']}" in rendered
    assert "rd_holds_corrupt 1" in rendered


@pytest.mark.unit
def test_unreadable_holds_file_fails_closed(tmp_path: Path) -> None:
    holds_mod.reset_holds_metrics()
    dir_path = tmp_path / "unreadable_holds.json"
    dir_path.mkdir()

    holds = holds_mod.HoldsList(dir_path, roles_loader=dict)
    blocker = holds.blocking("steward", "UpstreamDrift")

    assert blocker is not None
    assert blocker.id == holds_mod.FAIL_CLOSED_HOLD_ID
    assert "failing closed" in blocker.text
    assert holds_mod.holds_metrics_snapshot()["load_errors_total"] == 1

    bad_utf8 = tmp_path / "bad_utf8_holds.json"
    bad_utf8.write_bytes(b"\xff\xfe\x00\x00")
    utf8_holds = holds_mod.HoldsList(bad_utf8, roles_loader=dict)
    utf8_blocker = utf8_holds.blocking("steward")
    assert utf8_blocker is not None
    assert utf8_blocker.id == holds_mod.FAIL_CLOSED_HOLD_ID
    assert holds_mod.holds_metrics_snapshot()["load_errors_total"] == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [
        "123",
        '"just a string"',
        "true",
        json.dumps({"unexpected": []}),
        json.dumps({"holds": "not-a-list"}),
        json.dumps({"holds": ["not-an-object"]}),
        json.dumps({"holds": [{"text": ""}]}),
        json.dumps({"holds": [{"text": "valid text", "kind": "unknown-kind"}]}),
        json.dumps({"holds": [{"text": "valid text", "applies_to": []}]}),
        json.dumps({"holds": [{"text": "valid text", "applies_to": [123]}]}),
        json.dumps(
            {
                "holds": [
                    {"id": "hold-dup", "text": "first"},
                    {"id": "hold-dup", "text": "second"},
                ]
            }
        ),
    ],
)
def test_schema_invalid_holds_file_fails_closed_without_raising(tmp_path: Path, payload: str) -> None:
    holds_mod.reset_holds_metrics()
    path = tmp_path / "staff_holds.json"
    path.write_text(payload, encoding="utf-8")

    holds = holds_mod.HoldsList(path, roles_loader=dict)
    loaded = holds.load()
    blocker = holds.blocking("night-watch", "Runner_Dashboard")

    assert len(loaded) == 1
    assert blocker is not None
    assert blocker.id == holds_mod.FAIL_CLOSED_HOLD_ID
    assert blocker.kind == "schedule"
    assert "failing closed" in blocker.text
    assert holds_mod.holds_metrics_snapshot()["load_errors_total"] >= 1


@pytest.mark.unit
def test_failure_injection_denies_gated_dispatch_while_valid_file_unaffected(tmp_path: Path) -> None:
    holds_mod.reset_holds_metrics()
    roles = {
        "night-watch": roles_mod.parse_role(
            {
                "name": "night-watch",
                "providers": ["fake"],
                "schedule": "0 22 * * *",
                "window": {"start": "22:00", "end": "06:00"},
                "repos": ["UpstreamDrift"],
                "budget": {"usd_per_run": 3, "usd_per_day": 15},
            }
        )
    }

    # 1. Corrupt holds file fixture denies scheduled dispatch.
    corrupt_holds_path = tmp_path / "corrupt_staff_holds.json"
    corrupt_holds_path.write_text("<<<garbage>>>", encoding="utf-8")
    corrupt_store = store_mod.RunStore(tmp_path / "corrupt_runs.sqlite3")
    corrupt_runner = _FakeRunner(corrupt_store, roles)
    clock = {"now": datetime(2026, 9, 22, 21, 59, 0, tzinfo=LA)}
    corrupt_holds = holds_mod.HoldsList(corrupt_holds_path, roles_loader=dict)
    corrupt_guard = budget_mod.BudgetGuard(corrupt_store, alert_sink=lambda *_: None, clock=lambda: clock["now"])
    corrupt_sched = scheduler_mod.StaffScheduler(
        corrupt_runner,  # type: ignore[arg-type]
        corrupt_holds,
        corrupt_guard,
        clock=lambda: clock["now"],
        state_file=tmp_path / "corrupt_state.json",
    )
    assert corrupt_sched.tick() == []
    clock["now"] = datetime(2026, 9, 22, 22, 0, 5, tzinfo=LA)
    decisions = corrupt_sched.tick()
    assert len(decisions) == 1
    assert decisions[0]["fired"] is False
    assert "hold:" in decisions[0]["reason"] and "failing closed" in decisions[0]["reason"]
    assert corrupt_runner.submitted == []
    assert holds_mod.holds_metrics_snapshot()["load_errors_total"] >= 1

    # 2. Valid holds file fixture in the same process is unaffected and dispatches normally.
    valid_holds_path = tmp_path / "valid_staff_holds.json"
    valid_holds = holds_mod.HoldsList(valid_holds_path, roles_loader=dict)
    valid_holds.replace([{"text": "advisory only", "applies_to": ["*"], "kind": "guardrail"}])
    valid_raw_before = valid_holds_path.read_text(encoding="utf-8")

    valid_store = store_mod.RunStore(tmp_path / "valid_runs.sqlite3")
    valid_runner = _FakeRunner(valid_store, roles)
    valid_clock = {"now": datetime(2026, 9, 22, 21, 59, 0, tzinfo=LA)}
    valid_guard = budget_mod.BudgetGuard(valid_store, alert_sink=lambda *_: None, clock=lambda: valid_clock["now"])
    valid_sched = scheduler_mod.StaffScheduler(
        valid_runner,  # type: ignore[arg-type]
        valid_holds,
        valid_guard,
        clock=lambda: valid_clock["now"],
        state_file=tmp_path / "valid_state.json",
    )
    assert valid_sched.tick() == []
    valid_clock["now"] = datetime(2026, 9, 22, 22, 0, 5, tzinfo=LA)
    valid_decisions = valid_sched.tick()
    assert len(valid_decisions) == 1
    assert valid_decisions[0]["fired"] is True
    assert len(valid_runner.submitted) == 1
    assert valid_holds_path.read_text(encoding="utf-8") == valid_raw_before

    # Replacing the corrupt file with valid empty holds clears the corrupt gauge.
    corrupt_holds.replace([])
    assert holds_mod.holds_metrics_snapshot()["corrupt"] == 0
