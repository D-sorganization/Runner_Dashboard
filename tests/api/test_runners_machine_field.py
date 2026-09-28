"""Tests that _runner_response stamps a canonical `machine` field on each runner.

Regression coverage for D-sorganization/Runner_Dashboard#1738: the Fleet page
grouped runners by `name.split("-")[2]`, which is always "local" for
`d-sorg-local-<Host>-<n>` runner names, so no runner was ever bound to its
machine. The backend should stamp the canonical machine name using the
existing `infer_machine_from_runner_name` parser (no new parsing logic).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

from routers.runners import _runner_response  # noqa: E402


def _runner(name: str) -> dict:
    return {
        "id": 1,
        "name": name,
        "status": "online",
        "busy": False,
        "labels": [],
    }


def test_runner_response_stamps_machine_field() -> None:
    data = {
        "total_count": 4,
        "runners": [
            _runner("d-sorg-local-ControlTower-1"),
            _runner("d-sorg-local-Desktop-3"),
            _runner("d-sorg-local-Oglaptop-2"),
            _runner("d-sorg-local-ControlTower-windows-matlab-1"),
        ],
    }

    result = _runner_response(data, source="github")

    machines = {runner["name"]: runner["machine"] for runner in result["runners"]}
    assert machines == {
        "d-sorg-local-ControlTower-1": "ControlTower",
        "d-sorg-local-Desktop-3": "DeskComputer",
        "d-sorg-local-Oglaptop-2": "OGLaptop",
        "d-sorg-local-ControlTower-windows-matlab-1": "ControlTower",
    }


def test_runner_response_does_not_mutate_input_dicts() -> None:
    original = _runner("d-sorg-local-ControlTower-1")
    data = {"total_count": 1, "runners": [original]}

    result = _runner_response(data, source="github")

    assert "machine" not in original
    assert result["runners"][0]["machine"] == "ControlTower"
    assert result["runners"][0] is not original
