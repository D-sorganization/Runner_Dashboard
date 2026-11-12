"""backlog-push-spawn writes lane files before spawn when pool controller is enabled (#4279)."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location("bpush_pc", _ROOT / "scripts/barb-worker/backlog-push-spawn.py")
assert _spec and _spec.loader
spawn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spawn)


def test_spawn_writes_pending_lane_file_before_pid_when_flag_on(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    order: list[str] = []

    def fake_spawn(*a, **_):
        session = a[1]
        lane_path = log_dir / f"{session}.lane.json"
        order.append("spawn_called")
        assert lane_path.is_file()
        data = json.loads(lane_path.read_text(encoding="utf-8"))
        assert data["state"] == "pending"
        assert data["pid"] is None
        a[4].write_text("77", encoding="utf-8")
        return 77

    plan = [{"role": "lane", "session": "lane1", "prompt": "work"}]
    with patch.dict(
        os.environ,
        {"RUNNER_DASHBOARD_ROOT": str(_ROOT), "DASHBOARD_ENABLE_POOL_CONTROLLER": "1"},
    ):
        spawn._POOL_CONTROLLER_IMPORTS = None
        with patch.object(spawn, "_spawn_one", side_effect=fake_spawn):
            with patch.object(spawn.sys, "stdin", io.StringIO(json.dumps(plan))):
                assert spawn.main([str(log_dir)]) == 0
    final = json.loads((log_dir / "lane1.lane.json").read_text(encoding="utf-8"))
    assert final["state"] == "active"
    assert final["pid"] == 77
    assert order == ["spawn_called"]


def test_spawn_skips_lane_file_when_flag_off(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    def fake_spawn(*a, **_):
        a[4].write_text("5", encoding="utf-8")
        return 5

    plan = [{"role": "lane", "session": "lane1", "prompt": "work"}]
    with patch.dict(os.environ, {"RUNNER_DASHBOARD_ROOT": str(_ROOT)}, clear=False):
        os.environ.pop("DASHBOARD_ENABLE_POOL_CONTROLLER", None)
        spawn._POOL_CONTROLLER_IMPORTS = None
        with patch.object(spawn, "_spawn_one", side_effect=fake_spawn):
            with patch.object(spawn.sys, "stdin", io.StringIO(json.dumps(plan))):
                assert spawn.main([str(log_dir)]) == 0
    assert not (log_dir / "lane1.lane.json").exists()
