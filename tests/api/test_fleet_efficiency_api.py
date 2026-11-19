"""API coverage for fleet efficiency metadata (#4562)."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("fcntl", reason="coordination_fake_rm requires fcntl on POSIX")
from coordination_fake_rm import FakeRM, bot, install
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_BOT = {"Authorization": "Bearer bot-token", **_XHR}


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    principals = {"bot-token": bot("agent-codex")}
    yield from install(tmp_path, monkeypatch, principals, repos="Runner_Dashboard")


@pytest.fixture
def client(rm: FakeRM) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=("100.64.0.9", 51000))


def test_fleet_efficiency_endpoint(client: TestClient) -> None:
    resp = client.get("/api/coordination/fleet-efficiency")
    assert resp.status_code == 200
    body = resp.json()
    assert body["brief_line_cap"] == 6
    assert 300 <= body["wake_coalesce_sec"] <= 600
    assert body["planned_reset"]["min_turns"] >= 25