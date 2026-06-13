"""Tests for /_drain endpoint (issue #711)."""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _is_executable_script(path: Path) -> bool:
    if os.name != "nt":
        return bool(path.stat().st_mode & 0o111)
    rel = path.relative_to(_REPO_ROOT).as_posix()
    result = subprocess.run(
        ["git", "ls-files", "--stage", rel],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.startswith("100755 ")


def test_drain_mode_flag_exists():
    # Verify the drain-dashboard.sh script exists
    script = _REPO_ROOT / "deploy" / "drain-dashboard.sh"
    assert script.exists()
    assert _is_executable_script(script), "drain-dashboard.sh must be executable"


def test_drain_script_has_sigterm():
    """Drain script must send SIGTERM before SIGKILL."""
    script = Path(__file__).resolve().parents[2] / "deploy" / "drain-dashboard.sh"
    content = script.read_text()
    assert "SIGTERM" in content or "kill -TERM" in content.lower(), "drain-dashboard.sh must send SIGTERM"


def test_drain_script_has_timeout():
    """Drain script must enforce a timeout before SIGKILL."""
    script = Path(__file__).resolve().parents[2] / "deploy" / "drain-dashboard.sh"
    content = script.read_text()
    assert "DRAIN_TIMEOUT_S" in content, "drain-dashboard.sh must have a timeout"


# ─── Issue #939c: loopback guard must be an explicit check, not a bare assert ──


def _drain_source() -> str:
    return (Path(__file__).resolve().parents[2] / "backend" / "server.py").read_text(encoding="utf-8")


def test_drain_handler_has_no_bare_assert_guard():
    """The /_drain loopback guard must NOT be a bare `assert` (compiled out under -O).

    Greps the handler body to ensure the security check survives `python -O`.
    """
    import ast

    tree = ast.parse(_drain_source())
    drain_fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "drain_endpoint"
    )
    asserts = [n for n in ast.walk(drain_fn) if isinstance(n, ast.Assert)]
    assert not asserts, "drain_endpoint must not guard the loopback restriction with a bare assert (#939c)"


def test_drain_rejects_non_loopback_with_403():
    """A non-loopback client must get HTTP 403, regardless of optimization level."""
    import asyncio
    from unittest.mock import MagicMock

    from fastapi import HTTPException

    import server  # noqa: PLC0415

    req = MagicMock()
    req.client.host = "10.0.0.5"  # not loopback
    server._drain_mode = False
    try:
        asyncio.run(server.drain_endpoint(req))
    except HTTPException as exc:
        assert exc.status_code == 403
    else:  # pragma: no cover
        raise AssertionError("drain_endpoint must reject non-loopback clients with 403")
    assert server._drain_mode is False, "drain must not activate for a rejected client"


def test_drain_accepts_loopback():
    import asyncio
    from unittest.mock import MagicMock

    import server  # noqa: PLC0415

    req = MagicMock()
    req.client.host = "127.0.0.1"
    server._drain_mode = False
    try:
        result = asyncio.run(server.drain_endpoint(req))
        assert result["status"] == "draining"
        assert server._drain_mode is True
    finally:
        server._drain_mode = False
