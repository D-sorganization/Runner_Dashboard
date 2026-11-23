"""Staff e2e backend must import without deadlocking on trimmed rosters (#4605).

``run_token_scopes`` must not import ``action_executors`` at module load: that module
calls ``load_roles()`` during import and deadlocks when ``server`` is loading.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FAKES = REPO / "tests" / "e2e" / "fakes"


@pytest.mark.unit
def test_staff_e2e_backend_import_completes_under_e2e_roles_dir() -> None:
    """Regression: hermetic STAFF_ROLES_DIR must not hang ``import server`` (Playwright webServer)."""
    state = Path(tempfile.mkdtemp(prefix="rd-staff-e2e-import-"))
    (state / "home").mkdir()
    (state / "identity").mkdir()
    code = f"""
import os, sys, tempfile
from pathlib import Path
REPO = Path({str(REPO)!r})
os.environ.update({{
    "PYTHONPATH": str(REPO),
    "HOME": {str(state / "home")!r},
    "DASHBOARD_IDENTITY_DIR": {str(state / "identity")!r},
    "GH_TOKEN": "",
    "STAFF_ROLES_DIR": {str(FAKES / "roles")!r},
    "STAFF_E2E_HERMETIC": "1",
    "AUTODERIVE_FLEET_NODES": "0",
    "FLEET_NODES": "",
}})
os.chdir(REPO / "backend")
import server  # noqa: F401
print("ok")
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=25,
        env={k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "STAFF_ROLES_DIR")},
    )
    assert proc.returncode == 0, proc.stderr[-2000:] or proc.stdout
    assert "ok" in proc.stdout
