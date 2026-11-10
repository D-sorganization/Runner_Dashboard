"""Runner scheduler target override integration (#4308)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULER_PATH = REPO_ROOT / "deploy" / "runner-scheduler.py"


def _load_scheduler():
    spec = importlib.util.spec_from_file_location("runner_scheduler_target", SCHEDULER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["runner_scheduler_target"] = module
    spec.loader.exec_module(module)
    return module


def test_effective_capacity_uses_persisted_override(tmp_path: Path, monkeypatch) -> None:
    rs = _load_scheduler()
    override = tmp_path / "target-override.json"
    contract = rs.target_contract
    monkeypatch.setattr(contract, "TARGET_OVERRIDE_PATH", override)
    now = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
    contract.write_target_override(
        runners=2,
        reason="pytest",
        actor="pytest",
        before=6,
        until=(now + timedelta(hours=2)).isoformat(),
        path=override,
        created_at=now,
    )
    monkeypatch.setenv("RUNNER_SCHEDULER_NOW", now.isoformat())
    config = {"enabled": True, "default_count": 6, "max_count": 6, "schedules": []}
    desired, reason = rs.effective_capacity(config, None)
    assert desired == 2
    assert "target-override" in reason


def test_effective_capacity_reverts_to_schedule_after_expiry(tmp_path: Path, monkeypatch) -> None:
    rs = _load_scheduler()
    override = tmp_path / "target-override.json"
    contract = rs.target_contract
    monkeypatch.setattr(contract, "TARGET_OVERRIDE_PATH", override)
    start = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    expiry = start + timedelta(minutes=30)
    contract.write_target_override(
        runners=2,
        reason="pytest",
        actor="pytest",
        before=6,
        until=expiry.isoformat(),
        path=override,
        created_at=start,
    )
    config = {
        "enabled": True,
        "timezone": "UTC",
        "default_count": 5,
        "max_count": 6,
        "schedules": [],
    }
    monkeypatch.setenv("RUNNER_SCHEDULER_NOW", start.isoformat())
    assert rs.effective_capacity(config, None)[0] == 2
    after = expiry + timedelta(seconds=1)
    monkeypatch.setenv("RUNNER_SCHEDULER_NOW", after.isoformat())
    desired, reason = rs.effective_capacity(config, None)
    assert desired == 5
    assert reason == "default"
    assert contract.load_target_override(now=after) is None


def test_busy_runner_not_stopped_on_scale_down(monkeypatch, tmp_path: Path) -> None:
    rs = _load_scheduler()
    completed = rs.subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    def fake_cmd(cmd: list[str], **kwargs):  # noqa: ANN003
        if cmd[:2] == ["systemctl", "kill"]:
            return completed
        if cmd[:2] == ["systemctl", "stop"]:
            return completed
        if cmd[:3] == ["systemctl", "show", "-p"]:
            return rs.subprocess.CompletedProcess(args=cmd, returncode=0, stdout="inactive\n", stderr="")
        return completed

    monkeypatch.setattr(rs, "run_cmd", fake_cmd)
    units = [
        rs.RunnerUnit(1, "actions.runner-1", tmp_path / "r1", True, True),
        rs.RunnerUnit(2, "actions.runner-2", tmp_path / "r2", True, False),
    ]
    actions = rs.apply_capacity(units, 1, dry_run=False)
    stops = [a for a in actions if a["action"] == "stop"]
    assert len(stops) == 1
    assert stops[0]["runner"] == 2
