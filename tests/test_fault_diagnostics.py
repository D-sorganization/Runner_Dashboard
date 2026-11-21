"""Verify faulthandler stack-dump diagnostics are enabled at server startup."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = """
import faulthandler
import importlib.abc
import runpy
import sys

class StopApplicationImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "httpx":
            print(f"FAULTHANDLER_ENABLED={faulthandler.is_enabled()}", file=sys.stderr)
            raise RuntimeError("APPLICATION_IMPORT_REACHED")

sys.meta_path.insert(0, StopApplicationImports())
sys.argv = ["uvicorn", "server:asgi_app"]
runpy.run_path(SERVER_PATH, run_name="__main__")
""".replace("SERVER_PATH", repr(str(ROOT / "backend" / "server.py")))


def test_faulthandler_enabled_before_application_imports() -> None:
    env = {k: v for k, v in os.environ.items() if k not in {"WORKERS", "WEB_CONCURRENCY"}}
    result = subprocess.run(
        [sys.executable, "-c", BOOTSTRAP],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert "APPLICATION_IMPORT_REACHED" in result.stderr
    assert "FAULTHANDLER_ENABLED=True" in result.stderr
