"""Maxwell-Daemon proxy is retired (#1338, owner approval 2026-10-23).

Contract: no route under /api/maxwell is mounted on the dashboard app after
Maxwell_Daemon was archived (2026-10-22).
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import server  # noqa: E402

RETIRED_PREFIX = "/api/maxwell"


def test_maxwell_api_routes_are_not_mounted() -> None:
    mounted = [getattr(r, "path", "") for r in server.app.routes]
    assert [p for p in mounted if p.startswith(RETIRED_PREFIX)] == []


def test_maxwell_router_module_is_removed() -> None:
    assert not (_BACKEND / "routers" / "maxwell.py").exists()


def test_maxwell_contract_module_is_removed() -> None:
    assert not (_BACKEND / "maxwell_contract.py").exists()
