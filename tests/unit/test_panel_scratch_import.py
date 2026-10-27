"""panel_scratch must import with only ``backend/`` on the path (#2994).

The deploy artifact and ``python backend/server.py`` do not put the repo root
(which holds ``shared_scripts``) on ``sys.path``, so a top-level import of it
crashes backend startup.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def test_panel_scratch_imports_without_repo_root_on_path() -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONSTARTUP")}
    proc = subprocess.run(
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(BACKEND)!r}); import staff.panel_scratch"],
        cwd=str(BACKEND),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
