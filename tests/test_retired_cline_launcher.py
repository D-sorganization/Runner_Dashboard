"""The Cline Launcher is retired (#1338): its /api/agent-launcher surface is gone.

Contract: no route under /api/agent-launcher is mounted on the dashboard app, so
the unauthenticated-by-default agent spawner (#920) cannot come back through a
stale bookmark or client.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import server  # noqa: E402

RETIRED_PREFIX = "/api/agent-launcher"


def test_agent_launcher_routes_are_not_mounted() -> None:
    mounted = [getattr(r, "path", "") for r in server.app.routes]
    assert [p for p in mounted if p.startswith(RETIRED_PREFIX)] == []


def test_agent_launcher_module_is_removed() -> None:
    assert not (_BACKEND / "agent_launcher_router.py").exists()
