from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e.fakes.start_staff_backend import backend_env

pytestmark = pytest.mark.unit


def test_backend_env_preserves_loader_path_for_python_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Keep the real loader path after the marker: on self-hosted runners the
    # toolcache Python links a shared libpython that only loads through it.
    inherited = os.environ.get("LD_LIBRARY_PATH", "")
    loader_path = os.pathsep.join(filter(None, [str(tmp_path / "python-runtime"), inherited]))
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


def test_backend_env_pythonpath_has_repo_root_and_caller_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.e2e.fakes.start_staff_backend import REPO

    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "caller-libs"))

    child_env = backend_env(tmp_path, 5093)
    entries = child_env["PYTHONPATH"].split(os.pathsep)

    assert entries[0] == str(REPO)
    assert str(tmp_path / "caller-libs") in entries


def test_backend_env_imports_shared_scripts_from_backend_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tests.e2e.fakes.start_staff_backend import REPO

    monkeypatch.delenv("PYTHONPATH", raising=False)
    child = subprocess.run(
        [sys.executable, "-c", "import shared_scripts"],
        check=False,
        capture_output=True,
        cwd=REPO / "backend",
        env=backend_env(tmp_path, 5094),
        text=True,
    )

    assert child.returncode == 0, child.stderr
