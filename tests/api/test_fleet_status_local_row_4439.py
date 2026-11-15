"""The hub's own row in fleet status reports the same runner counts as ``/api/system`` (#4439).

The row was built from a bare ``get_system_metrics_snapshot()`` call, so it had no runner
capacity: ``desired`` 0, ``online`` 0 and the fallback cap of 16 while the host ran four runners.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import routers.fleet as fleet  # noqa: E402
import routers.system as system  # noqa: E402
import system_utils  # noqa: E402

_CAPACITY = {
    "configured_runners": 8,
    "max_runners": 6,
    "state": {"desired": 4, "online": 4, "busy": 1, "reason": "weekend"},
}
_RUNNERS = [{"runner": f"runner-{n}", "status": "running"} for n in range(1, 5)]
_COMPARED = ("desired", "online", "busy", "cap")


@pytest.fixture
def four_runners(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host whose scheduler wants 4 of 8 installed runners, with a cap of 6."""
    monkeypatch.setattr(system, "_get_runner_capacity_snapshot", lambda: dict(_CAPACITY))
    monkeypatch.setattr(system, "_boot_time", 1.0)
    monkeypatch.setattr(system, "_host_memory_gb", 64.0)
    monkeypatch.setattr(system, "runner_limit", lambda: 6)
    monkeypatch.setattr(system_utils, "get_per_runner_resources", lambda limit: list(_RUNNERS))


@pytest.fixture
def hub_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fleet status with no nodes and no peer pools: only the local row is built."""
    monkeypatch.setattr(fleet, "load_machine_registry", lambda: {})
    monkeypatch.setattr(fleet, "derive_pool_topology", lambda *_a, **_k: (None, []))
    monkeypatch.setattr(fleet, "FLEET_NODES", {})
    monkeypatch.setattr(fleet, "HOSTNAME", "hub-under-test")
    monkeypatch.setattr(fleet, "_record_fleet_events", lambda _r: None)


async def _local_row() -> dict[str, Any]:
    responses = await fleet._collect_fleet_status(exclude_pools=True)
    return responses["hub-under-test"]


@pytest.mark.asyncio
async def test_local_row_host_health_matches_api_system(four_runners: None, hub_alone: None) -> None:
    row = await _local_row()
    api_system = await system.get_system_metrics()

    assert {k: row["host_health"][k] for k in _COMPARED} == {k: api_system["host_health"][k] for k in _COMPARED}
    assert row["host_health"]["desired"] == 4
    assert row["host_health"]["cap"] == 6


@pytest.mark.asyncio
async def test_local_row_carries_runner_capacity_and_processes(four_runners: None, hub_alone: None) -> None:
    row = await _local_row()
    api_system = await system.get_system_metrics()

    assert row["runner_capacity"] == api_system["runner_capacity"] == _CAPACITY
    assert row["runner_processes"] == api_system["runner_processes"] == _RUNNERS


@pytest.mark.asyncio
async def test_api_system_is_the_shared_local_snapshot(four_runners: None) -> None:
    """``/api/system`` and every other local reader go through one helper, so they cannot drift."""
    helper = await system.local_system_metrics_snapshot()
    route = await system.get_system_metrics()

    assert helper["runner_capacity"] == route["runner_capacity"] == _CAPACITY
    assert helper["host_health"]["cap"] == route["host_health"]["cap"] == 6


def _bare_snapshot_calls(path: Path) -> list[int]:
    """Lines that call or pass ``get_system_metrics_snapshot`` itself, which carries no runner capacity."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "get_system_metrics_snapshot":
                lines.append(node.lineno)
            for kw in node.keywords:
                if isinstance(kw.value, ast.Name) and kw.value.id == "get_system_metrics_snapshot":
                    lines.append(kw.value.lineno)
    return sorted(lines)


@pytest.mark.parametrize("module", ["routers/fleet.py", "server.py"])
def test_local_readers_do_not_use_the_bare_snapshot(module: str) -> None:
    """Fleet status and the routes wired in ``server.py`` (host health, node system) read the helper."""
    assert _bare_snapshot_calls(_BACKEND / module) == []
