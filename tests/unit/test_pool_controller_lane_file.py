"""Tests for pool-controller lane file write-before-spawn (#4279, C-10)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from pool_controller.lane_file import (
    LANE_FILE_SCHEMA_VERSION,
    LaneFileError,
    read_lane_file,
    transition_lane_file,
    write_lane_file,
)
from pool_controller.lane_state import LaneState


def test_write_lane_file_before_spawn_shape(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    path = write_lane_file(
        log_dir,
        session="lane-a",
        role="lane",
        state=LaneState.PENDING,
    )
    assert path.name == "lane-a.lane.json"
    assert path.stat().st_size > 0
    before_spawn = time.time()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == LANE_FILE_SCHEMA_VERSION
    assert data["session"] == "lane-a"
    assert data["role"] == "lane"
    assert data["state"] == "pending"
    assert data["pid"] is None
    assert data["updated_at"] <= time.time()
    assert data["updated_at"] >= before_spawn - 1


def test_transition_lane_file_updates_state_and_pid(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    write_lane_file(log_dir, "sup", "supervisor", LaneState.PENDING)
    path = transition_lane_file(log_dir, "sup", LaneState.ACTIVE, pid=99)
    data = read_lane_file(path)
    assert data["state"] == "active"
    assert data["pid"] == 99


def test_transition_lane_file_rejects_illegal_transition(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    write_lane_file(log_dir, "lane1", "lane", LaneState.ACTIVE, pid=1)
    with pytest.raises(LaneFileError, match="transition"):
        transition_lane_file(log_dir, "lane1", LaneState.PENDING)
