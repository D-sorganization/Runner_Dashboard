"""Peer pools on the same host must not recurse into /api/fleet/status over HTTP."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import routers.fleet as fleet  # noqa: E402


@pytest.mark.asyncio
async def test_local_peer_pool_probes_system_not_fleet_status(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_a: object) -> bool:
            return False

        async def get(self, url: str, **_k: object) -> object:
            seen.append(url)

            class _Resp:
                status_code = 200

                def json(self) -> dict:
                    return {"hostname": "local-pool"}

            return _Resp()

    monkeypatch.setattr(fleet.httpx, "AsyncClient", lambda *_a, **_k: _Client())

    local = frozenset({"http://127.0.0.1:8321"})
    result = await fleet._fetch_peer_pool(
        {"name": "ControlTower-SSD", "url": "http://127.0.0.1:8321"},
        local_dashboard_urls=local,
    )

    assert seen == ["http://127.0.0.1:8321/api/system"]
    assert result == {"ControlTower-SSD": {"hostname": "local-pool"}}
