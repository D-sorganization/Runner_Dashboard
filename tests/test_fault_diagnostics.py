"""Verify faulthandler stack-dump diagnostics are enabled at server startup."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = str(ROOT / "backend" / "server.py")

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
""".replace("SERVER_PATH", repr(SERVER_PATH))

SIGUSR1_BOOTSTRAP = """
import importlib.abc
import runpy
import sys
import threading

started = threading.Event()
release = threading.Event()

def _fault_diag_bg_worker():
    started.set()
    release.wait(timeout=10.0)

class PauseAtApplicationImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "httpx":
            t = threading.Thread(target=_fault_diag_bg_worker, name="fault-diag-worker", daemon=True)
            t.start()
            started.wait(timeout=5.0)
            print("READY", flush=True)
            sys.stdin.readline()
            release.set()
            t.join(timeout=2.0)
            raise SystemExit(0)

sys.meta_path.insert(0, PauseAtApplicationImports())
sys.argv = ["uvicorn", "server:asgi_app"]
try:
    runpy.run_path(SERVER_PATH, run_name="__main__")
except SystemExit as exc:
    sys.exit(exc.code)
""".replace("SERVER_PATH", repr(SERVER_PATH))


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


def test_sigusr1_dumps_all_threads_and_process_survives() -> None:
    sigusr1 = getattr(signal, "SIGUSR1", None)
    if sigusr1 is None:
        pytest.skip("SIGUSR1 not available on this platform")
    env = {k: v for k, v in os.environ.items() if k not in {"WORKERS", "WEB_CONCURRENCY"}}
    proc = subprocess.Popen(
        [sys.executable, "-c", SIGUSR1_BOOTSTRAP],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        ready = proc.stdout.readline().strip()
        assert ready == "READY"
        os.kill(proc.pid, sigusr1)
        time.sleep(0.2)
        assert proc.poll() is None, "SIGUSR1 should not terminate the server process"
        _stdout, stderr = proc.communicate(input="\n", timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    assert proc.returncode == 0
    assert "Current thread" in stderr or "Thread" in stderr
    assert "_fault_diag_bg_worker" in stderr
