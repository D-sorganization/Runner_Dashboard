"""Tests for backend.disk_full_cordon_runtime persistence helpers."""

from __future__ import annotations

from disk_full_cordon import DiskFullCordonState
from disk_full_cordon_runtime import cordon_status_dict, load_cordon_state, save_cordon_state


def test_cordon_state_round_trip(tmp_path) -> None:
    path = tmp_path / "cordon-state.json"
    state = DiskFullCordonState(
        phase="tripping",
        tripped_at=42.0,
        cleanup_done=False,
        last_proactive_cleanup_at=123.5,
    )
    save_cordon_state(state, path=path)
    loaded = load_cordon_state(path=path)
    assert loaded.phase == "tripping"
    assert loaded.tripped_at == 42.0
    assert loaded.cleanup_done is False
    assert loaded.last_proactive_cleanup_at == 123.5


def test_cordon_status_dict_reports_inactive_when_normal() -> None:
    status = cordon_status_dict(DiskFullCordonState())
    assert status["active"] is False
    assert status["phase"] == "normal"
    assert status["scheduling_inhibited"] is False
