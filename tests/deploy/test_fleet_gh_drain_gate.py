"""Fleet-gh shim enforces drain gate on gh pr create (#3915 slice 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

_FLEET_GH_DIR = Path(__file__).resolve().parents[2] / "deploy" / "fleet-gh"
_GH_SHIM = _FLEET_GH_DIR / "gh"


@pytest.mark.unit
def test_fleet_gh_shim_wires_drain_gate_before_exec() -> None:
    text = _GH_SHIM.read_text(encoding="utf-8")
    assert "check_drain_gate_for_pr_create" in text
    assert "FLEET_GH_BACKEND_ROOT" in text
