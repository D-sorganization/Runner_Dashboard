"""Cline Launcher is retired (SC-G6, issue #1338).

Owner decision 2026-09-25: Staff Hub roles and the CLI agent tiers replace the
Cline launcher. The dashboard no longer serves ``/api/agent-launcher/*``, and
the published OpenAPI contract no longer advertises it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

import server  # noqa: E402

_RETIRED_PREFIX = "/api/agent-launcher"


def test_server_mounts_no_agent_launcher_routes() -> None:
    paths = [getattr(route, "path", "") for route in server.app.routes]
    assert not [p for p in paths if p.startswith(_RETIRED_PREFIX)]


def test_published_openapi_has_no_agent_launcher_paths() -> None:
    spec = json.loads((_ROOT / "frontend/src/lib/openapi.json").read_text(encoding="utf-8"))
    assert not [p for p in spec.get("paths", {}) if p.startswith(_RETIRED_PREFIX)]
