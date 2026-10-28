"""Backend must import cleanly under the Docker CMD's invocation shape (#3248).

The production image runs ``python -m backend.server`` from ``WORKDIR /app``
with ``PYTHONPATH=/app`` (see ``Dockerfile``). Under ``-m``, ``sys.path[0]``
is the process cwd (``/app``), not ``/app/backend``, so any sibling import in
``backend/server.py`` that resolves only because ``backend/`` happens to be on
``sys.path`` (true for the ``cd backend && python server.py`` shape covered by
``test_backend_cwd_imports.py``) must still succeed here too.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_server_module_imports_under_docker_cmd_invocation() -> None:
    """Pre: cwd=repo root, PYTHONPATH=repo root, backend/ not pre-added. Post: import succeeds."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO)
    env.setdefault("AUTODERIVE_FLEET_NODES", "0")
    env.setdefault("FLEET_NODES", "")
    proc = subprocess.run(
        [sys.executable, "-c", "import backend.server"],
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"backend.server import failed under docker-style invocation (rc={proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
