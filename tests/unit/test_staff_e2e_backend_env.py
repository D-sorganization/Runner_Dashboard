from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e.fakes.start_staff_backend import backend_env

pytestmark = pytest.mark.unit


def test_backend_env_preserves_loader_path_for_python_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Prepend a marker instead of replacing the variable: on setup-python runners the
    # child interpreter itself needs the real runtime directory to load libpython.
    marker = str(tmp_path / "python-runtime")
    loader_path = os.pathsep.join(filter(None, [marker, os.environ.get("LD_LIBRARY_PATH")]))
    monkeypatch.setenv("LD_LIBRARY_PATH", loader_path)
    monkeypatch.setenv("STAFF_E2E_HOST_SECRET", "must-not-be-forwarded")

    child_env = backend_env(tmp_path, 5092)
    child_script = """
import os
import sys

if not os.environ.get("LD_LIBRARY_PATH"):
    print("missing Python runtime loader path", file=sys.stderr)
    raise SystemExit(127)
if "STAFF_E2E_HOST_SECRET" in os.environ:
    print("unrelated host secret leaked", file=sys.stderr)
    raise SystemExit(42)
"""
    child = subprocess.run(
        [sys.executable, "-c", child_script],
        check=False,
        capture_output=True,
        env=child_env,
        text=True,
    )

    assert child.returncode == 0, child.stderr
    assert child_env["LD_LIBRARY_PATH"] == loader_path
    assert "STAFF_E2E_HOST_SECRET" not in child_env
