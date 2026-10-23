"""Backend must import cleanly when the process cwd is ``backend/`` (#2215 follow-up).

Production DeskComputer runs ``cd backend && python server.py``. Only ``backend/``
is on ``sys.path`` (via ``server.py``); the repo-root ``shared_scripts/`` package
must not be required for disk-guard imports.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_BACKEND = _REPO / "backend"

_PROBE = """
import sys
from pathlib import Path

backend = Path.cwd()
sys.path.insert(0, str(backend))
# Ensure repo-root shared_scripts is not accidentally visible.
sys.path[:] = [p for p in sys.path if not p.endswith("shared_scripts")]

import fleet_events  # noqa: F401
import runner_autoscaler  # noqa: F401
"""


@pytest.mark.unit
def test_backend_cwd_contract_is_not_a_marker_file() -> None:
    """No guard reads ``backend/.entry-cwd-backend``; cwd contract is behavioral tests only."""
    marker = _BACKEND / ".entry-cwd-backend"
    assert not marker.exists(), (
        "Remove backend/.entry-cwd-backend — document cwd=backend in this module instead "
        "(see test_disk_guard_modules_import_with_backend_cwd_only)."
    )


@pytest.mark.unit
def test_disk_guard_modules_import_with_backend_cwd_only() -> None:
    """Pre: cwd is backend/ without repo root on PYTHONPATH. Post: imports succeed."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = ""
    env.setdefault("AUTODERIVE_FLEET_NODES", "0")
    env.setdefault("FLEET_NODES", "")
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=_BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"backend-only import failed (rc={proc.returncode})\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
