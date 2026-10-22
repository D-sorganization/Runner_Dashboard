"""Barb CLI argument wiring (#2157 slice 6)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

_FLEET = Path(__file__).resolve().parents[2] / "clients" / "fleet"
if str(_FLEET) not in sys.path:
    sys.path.insert(0, str(_FLEET))

import barb  # noqa: E402


def test_project_new_calls_client() -> None:
    client = mock.Mock()
    client.staff_project_create.return_value = {"id": "prj_x", "title": "T"}
    with mock.patch.object(barb, "_client", return_value=client):
        code = barb.cmd_project_new(
            client,
            type("NS", (), {"title": "T", "goal": "G", "repo": "Runner_Dashboard", "done": "", "decompose": False})(),
        )
    assert code == 0
    client.staff_project_create.assert_called_once()
