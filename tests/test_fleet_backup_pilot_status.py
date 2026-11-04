"""Fleet backup pilot last-success marker on ``/api/fleet/status`` (#4015)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fleet_backup_pilot_status import (  # noqa: E402
    MARKER_INLINE_MAX_BYTES,
    read_fleet_backup_pilot_status,
)


def test_read_status_missing_file_on_non_pilot_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_HOSTNAME", "DeskComputer")
    monkeypatch.delenv("FLEET_BACKUP_PILOT", raising=False)
    payload = read_fleet_backup_pilot_status()
    assert payload["enabled"] is False


def test_read_status_from_small_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_BACKUP_PILOT", "1")
    state = tmp_path / "fleet_backup_pilot.json"
    body = {
        "last_success_at": "2026-11-04T12:00:00Z",
        "last_result": "success",
        "bandwidth_mbps": 42.5,
        "bandwidth_probe_completed_at": "2026-11-04T11:00:00Z",
    }
    raw = json.dumps(body).encode("utf-8")
    assert len(raw) <= MARKER_INLINE_MAX_BYTES
    state.write_bytes(raw)
    monkeypatch.setenv("RUNNER_DASHBOARD_FLEET_BACKUP_PILOT_STATE", str(state))
    payload = read_fleet_backup_pilot_status()
    assert payload["enabled"] is True
    assert payload["last_success_at"] == "2026-11-04T12:00:00Z"
    assert payload["last_result"] == "success"
    assert payload["bandwidth_mbps"] == 42.5
    assert payload["marker"] == body


def test_read_status_omits_inline_marker_when_large(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_BACKUP_PILOT", "1")
    state = tmp_path / "fleet_backup_pilot.json"
    body = {
        "last_success_at": "2026-11-04T12:00:00Z",
        "last_result": "success",
        "notes": "x" * (MARKER_INLINE_MAX_BYTES + 50),
    }
    state.write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setenv("RUNNER_DASHBOARD_FLEET_BACKUP_PILOT_STATE", str(state))
    payload = read_fleet_backup_pilot_status()
    assert payload["enabled"] is True
    assert payload["last_success_at"] == "2026-11-04T12:00:00Z"
    assert "marker" not in payload
    assert payload["marker_truncated"] is True


def test_read_status_corrupt_json_sets_attention(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_BACKUP_PILOT", "1")
    state = tmp_path / "fleet_backup_pilot.json"
    state.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv("RUNNER_DASHBOARD_FLEET_BACKUP_PILOT_STATE", str(state))
    payload = read_fleet_backup_pilot_status()
    assert payload["attention"] is True
    assert payload["last_result"] == "error"
