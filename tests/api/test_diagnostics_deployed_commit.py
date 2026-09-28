"""Tests for diagnostics summary git_commit fallback on artifact installs (issue #1748).

Artifact-installed hubs have no ``.git`` directory, so ``git rev-parse --short HEAD``
returns empty output. The diagnostics summary should then fall back to the deployed
commit recorded in the deployment metadata (``server.py``'s ``_deployment_info``),
supplied to this router via ``set_deployment_info_getter`` — never by importing
``server`` directly (that would create a circular import).

TDD: these tests are written before the implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from routers import diagnostics as diagnostics_router  # noqa: E402


class _FakeCompleted:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


def _fake_run_git_empty(cmd: list[str], **_kwargs: Any) -> _FakeCompleted:
    """Simulate `git rev-parse --short HEAD` returning empty output (no .git dir)."""
    if cmd[:2] == ["git", "rev-parse"]:
        return _FakeCompleted(stdout="")
    # Any other subprocess call (e.g. `wsl -l -v`) — pretend unavailable.
    raise OSError("subprocess unavailable in test")


@pytest.fixture(autouse=True)
def _reset_deployment_info_getter() -> Any:
    """Ensure each test starts with no registered getter and cleans up after itself."""
    diagnostics_router.set_deployment_info_getter(None)  # type: ignore[arg-type]
    yield
    diagnostics_router.set_deployment_info_getter(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_git_commit_falls_back_to_deployment_git_sha_when_rev_parse_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When `git rev-parse` yields nothing, use the deployment metadata's git_sha."""
    monkeypatch.setattr(diagnostics_router.subprocess, "run", _fake_run_git_empty)

    diagnostics_router.set_deployment_info_getter(
        lambda: {"git_sha": "157232e6f7290686b28b6ded4f19e2a8bbd6cb13"}  # pragma: allowlist secret
    )

    summary = await diagnostics_router.get_diagnostics_summary()

    assert summary["git_commit"] == "157232e6"


@pytest.mark.asyncio
async def test_git_commit_is_unknown_when_deployment_git_sha_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the deployment metadata's git_sha is itself "unknown", fall back to "unknown"."""
    monkeypatch.setattr(diagnostics_router.subprocess, "run", _fake_run_git_empty)

    diagnostics_router.set_deployment_info_getter(lambda: {"git_sha": "unknown"})

    summary = await diagnostics_router.get_diagnostics_summary()

    assert summary["git_commit"] == "unknown"


@pytest.mark.asyncio
async def test_git_commit_is_unknown_when_no_getter_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a registered deployment-info getter, fall back to "unknown" (never crash)."""
    monkeypatch.setattr(diagnostics_router.subprocess, "run", _fake_run_git_empty)

    summary = await diagnostics_router.get_diagnostics_summary()

    assert summary["git_commit"] == "unknown"
