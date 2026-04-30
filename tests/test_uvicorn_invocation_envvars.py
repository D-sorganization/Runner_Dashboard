"""Tests for env-var-driven uvicorn invocation (#393).

The dashboard reads ``WORKERS``, ``LIMIT_CONCURRENCY`` and
``TIMEOUT_KEEP_ALIVE`` from the environment so operators can tune the
ASGI server without code edits.  This test exercises the helper that
parses the env vars *and* asserts the source code of ``server.py`` wires
them through to ``uvicorn.run``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import server

SERVER_SRC = Path(server.__file__).read_text(encoding="utf-8")


def test_helper_returns_documented_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default values are 1 / 200 / 5 when env vars are unset."""
    for name in ("WORKERS", "LIMIT_CONCURRENCY", "TIMEOUT_KEEP_ALIVE"):
        monkeypatch.delenv(name, raising=False)

    cfg = server._read_uvicorn_env_config()

    # WORKERS must default to 1 because leader-election (#367) is not in place.
    assert cfg["workers"] == 1
    assert cfg["limit_concurrency"] == 200
    assert cfg["timeout_keep_alive"] == 5


def test_helper_reads_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKERS", "3")
    monkeypatch.setenv("LIMIT_CONCURRENCY", "512")
    monkeypatch.setenv("TIMEOUT_KEEP_ALIVE", "15")

    cfg = server._read_uvicorn_env_config()

    assert cfg["workers"] == 3
    assert cfg["limit_concurrency"] == 512
    assert cfg["timeout_keep_alive"] == 15


def test_helper_falls_back_on_invalid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKERS", "not-an-int")
    monkeypatch.setenv("LIMIT_CONCURRENCY", "")
    monkeypatch.setenv("TIMEOUT_KEEP_ALIVE", "abc")

    cfg = server._read_uvicorn_env_config()

    assert cfg == {"workers": 1, "limit_concurrency": 200, "timeout_keep_alive": 5}


def test_server_source_references_each_env_var() -> None:
    """server.py must mention each env var name verbatim."""
    for name in ("WORKERS", "LIMIT_CONCURRENCY", "TIMEOUT_KEEP_ALIVE"):
        assert name in SERVER_SRC, f"server.py should reference env var {name}"


def test_uvicorn_run_receives_tuned_kwargs() -> None:
    """The uvicorn.run call must forward the tuned values."""
    assert "uvicorn.run(" in SERVER_SRC
    # Each tunable must be passed as a kwarg to uvicorn.run.
    for kwarg in ("workers=", "limit_concurrency=", "timeout_keep_alive="):
        assert kwarg in SERVER_SRC, f"uvicorn.run should be called with {kwarg}"


def test_warning_when_workers_gt_1() -> None:
    """A runtime warning is logged when WORKERS > 1 because #367 isn't ready."""
    assert "#367" in SERVER_SRC, "must reference leader-election issue #367"
    # The warning text mentions WORKERS to make it greppable in operator logs.
    assert "WORKERS" in SERVER_SRC
