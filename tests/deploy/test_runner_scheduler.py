"""Tests for deploy/runner-scheduler.py capacity planning and provenance."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULER_PATH = REPO_ROOT / "deploy" / "runner-scheduler.py"

spec = importlib.util.spec_from_file_location("runner_scheduler", SCHEDULER_PATH)
assert spec and spec.loader
runner_scheduler = importlib.util.module_from_spec(spec)
sys.modules["runner_scheduler"] = runner_scheduler
spec.loader.exec_module(runner_scheduler)


def test_desired_capacity_with_manual_target_provenance() -> None:
    config = {"enabled": True, "default_count": 4, "schedules": []}
    desired, reason = runner_scheduler.desired_capacity(config, target_override=1, target_source="cli: --target")
    assert desired == 1
    assert reason == "manual-target (cli: --target)"


def test_desired_capacity_with_manual_target_without_source() -> None:
    config = {"enabled": True, "default_count": 4, "schedules": []}
    desired, reason = runner_scheduler.desired_capacity(config, target_override=2)
    assert desired == 2
    assert reason == "manual-target"


def test_desired_capacity_schedule_disabled() -> None:
    config = {"enabled": False, "default_count": 3, "schedules": []}
    desired, reason = runner_scheduler.desired_capacity(config)
    assert desired == 3
    assert reason == "schedule-disabled"


def test_desired_capacity_default_fallback() -> None:
    config = {"enabled": True, "default_count": 5, "schedules": []}
    desired, reason = runner_scheduler.desired_capacity(config)
    assert desired == 5
    assert reason == "default"
