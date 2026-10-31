"""The fake Staff Console e2e backend must disable the production resource gate (#3631)."""

from __future__ import annotations

from pathlib import Path

import start_staff_backend as backend


def test_backend_env_disables_dispatch_resource_gate(tmp_path: Path) -> None:
    env = backend.backend_env(tmp_path, port=5001)
    assert env["STAFF_DISPATCH_RESOURCE_GATE"] == "0"
