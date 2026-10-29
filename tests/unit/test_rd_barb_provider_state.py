"""Tests for rd-barb active provider persistence (#3527)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from staff import rd_barb_provider_state as ps


def test_record_and_read_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path))
    ps.record_active_provider("rd-barb", "codex", model="gpt-6-luna", failure_class="rate_limited", previous="claude")
    snap = ps.read_active_provider("rd-barb")
    assert snap is not None
    assert snap.provider == "codex"
    assert snap.model == "gpt-6-luna"
    assert snap.previous == "claude"
    assert ps.active_provider_payload()["provider"] == "codex"
