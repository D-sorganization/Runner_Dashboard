"""Lane file v2 schema, v1 upgrade path, and pool.lock atomic writes (#4279, ADR 0012)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pool_controller.lane_file import (
    LANE_FILE_SCHEMA_VERSION_V2,
    LaneFileError,
    read_lane_file,
    write_lane_file,
)
from pool_controller.lane_state import LaneState


def test_write_lane_file_v2_schema_and_pool_lock(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    path = write_lane_file(
        log_dir,
        session="lane-a",
        role="lane",
        state=LaneState.PENDING,
        push_id="push-1",
        host="test-host",
    )
    assert (log_dir / "pool.lock").is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == LANE_FILE_SCHEMA_VERSION_V2
    assert data["push_id"] == "push-1"
    assert data["host"] == "test-host"
    assert data["provider"] is None
    assert data["claim"] is None
    assert data["created_at"] == data["updated_at"]


def test_read_lane_file_upgrades_v1_with_null_v2_fields(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    v1 = {
        "schema_version": "pool-controller-lane.v1",
        "session": "s1",
        "role": "lane",
        "state": "active",
        "pid": 10,
        "updated_at": 100.0,
    }
    path = log_dir / "s1.lane.json"
    path.write_text(json.dumps(v1, indent=2), encoding="utf-8")
    record = read_lane_file(path)
    assert record["schema_version"] == "pool-controller-lane.v1"
    assert record["provider"] is None
    assert record["push_id"] is None
    assert record["host"] is None
    assert record["claim"] is None
    assert record["created_at"] is None


def test_write_lane_file_uses_atomic_replace_not_inplace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    writes: list[str] = []
    real_replace = os.replace

    def tracking_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        writes.append(f"replace:{src}->{dst}")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", tracking_replace)
    write_lane_file(log_dir, "x", "lane", LaneState.PENDING)
    assert any("replace:" in w for w in writes)
    assert not any(log_dir.glob(".tmp-*.json"))


def test_read_lane_file_rejects_unknown_schema(tmp_path: Path) -> None:
    path = tmp_path / "bad.lane.json"
    path.write_text(json.dumps({"schema_version": "pool-controller-lane.v99"}), encoding="utf-8")
    with pytest.raises(LaneFileError, match="unsupported"):
        read_lane_file(path)
