"""routers.staff_pr_hygiene must import under the production launch contract (#3311 follow-up).

Both ``start-dashboard.sh`` (``exec python "${BACKEND_DIR}/server.py"``) and the
Playwright E2E harness (``scripts/start_e2e_backend.py``, ``cwd=backend/``) launch
the backend the same way test_backend_cwd_imports.py documents: only ``backend/``
on ``sys.path``, repo root absent. ``routers.staff_pr_hygiene`` imports the
repo-root ``scripts`` package (``from scripts import automerge_guard``), which
that launch contract does not provide.
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

import routers.staff_pr_hygiene  # noqa: F401
"""


@pytest.mark.unit
def test_staff_pr_hygiene_imports_with_backend_cwd_only() -> None:
    """Pre: cwd is backend/ without repo root on PYTHONPATH. Post: import succeeds."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = ""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=_BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"routers.staff_pr_hygiene import failed under the production launch contract "
        f"(rc={proc.returncode})\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
