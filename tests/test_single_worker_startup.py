"""Single-worker startup contract, exercised before application state is loaded."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Stop at the first third-party import: accepted configurations must reach it,
# rejected configurations must exit earlier, without loading operator state.
BOOTSTRAP = """
import importlib.abc
import runpy
import sys
class StopApplicationImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'httpx':
            raise RuntimeError('APPLICATION_IMPORT_REACHED')
sys.meta_path.insert(0, StopApplicationImports())
sys.argv = ['uvicorn', 'server:asgi_app', *sys.argv[2:]]
runpy.run_path(SERVER_PATH, run_name='__main__')
""".replace("SERVER_PATH", repr(str(ROOT / "backend" / "server.py")))


def startup(env_overrides: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key not in {"WORKERS", "WEB_CONCURRENCY"}}
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", BOOTSTRAP, "server:asgi_app", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize(
    ("env", "args"),
    [({"WORKERS": "2"}, ()), ({"WEB_CONCURRENCY": "2"}, ()), ({}, ("--workers", "2")), ({}, ("--workers=2",))],
)
def test_workers_gt1_refuses_start(env: dict[str, str], args: tuple[str, ...]) -> None:
    result = startup(env, *args)
    assert result.returncode != 0
    assert "single-worker" in result.stderr
    assert "process-local" in result.stderr
    assert "APPLICATION_IMPORT_REACHED" not in result.stderr


@pytest.mark.parametrize("env", [{}, {"WORKERS": "1"}, {"WEB_CONCURRENCY": "1"}])
def test_single_worker_reaches_application_imports(env: dict[str, str]) -> None:
    assert "APPLICATION_IMPORT_REACHED" in startup(env).stderr


def test_readme_documents_single_worker_only() -> None:
    readme = (ROOT / "README.md").read_text()
    assert "single-worker is the only supported mode" in readme
    assert "If `WORKERS > 1` is configured" not in readme
