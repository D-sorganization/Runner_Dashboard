"""Tests for GET /api/deployment/git-drift unknown-commit handling (issue #1748).

Artifact-installed hubs have no ``.git`` directory, so `git rev-parse HEAD` and/or
`git rev-parse origin/main` return empty output. `get_git_drift` must not claim
"up to date" when it has no commits to compare — it must report an unknown state.

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

from routers import deployment as deployment_router  # noqa: E402


class _FakeCompleted:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


def _fake_run_both_empty(cmd: list[str], **_kwargs: Any) -> _FakeCompleted:
    """Simulate both HEAD and origin/main lookups returning empty output."""
    return _FakeCompleted(stdout="")


def _fake_run_only_remote_empty(cmd: list[str], **_kwargs: Any) -> _FakeCompleted:
    """Simulate HEAD present but origin/main missing (no remote configured)."""
    if cmd == ["git", "rev-parse", "HEAD"]:
        return _FakeCompleted(stdout="157232e6f7290686b28b6ded4f19e2a8bbd6cb13\n")  # pragma: allowlist secret
    return _FakeCompleted(stdout="")


def _fake_run_both_present_same(cmd: list[str], **_kwargs: Any) -> _FakeCompleted:
    return _FakeCompleted(stdout="157232e6f7290686b28b6ded4f19e2a8bbd6cb13\n")  # pragma: allowlist secret


def _fake_run_both_present_different(cmd: list[str], **_kwargs: Any) -> _FakeCompleted:
    if cmd == ["git", "rev-parse", "HEAD"]:
        return _FakeCompleted(stdout="157232e6f7290686b28b6ded4f19e2a8bbd6cb13\n")  # pragma: allowlist secret
    return _FakeCompleted(stdout="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")  # pragma: allowlist secret


@pytest.mark.asyncio
async def test_git_drift_unknown_when_no_git_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When neither commit is available (artifact install), report an unknown state."""
    monkeypatch.setattr(deployment_router.subprocess, "run", _fake_run_both_empty)

    result = await deployment_router.get_git_drift()

    assert result["is_drifted"] is None
    assert result["drift_details"] == "unknown (no git checkout; see /api/deployment/drift)"


@pytest.mark.asyncio
async def test_git_drift_unknown_when_remote_commit_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When only the remote commit is missing, still report an unknown state (not "up to date")."""
    monkeypatch.setattr(deployment_router.subprocess, "run", _fake_run_only_remote_empty)

    result = await deployment_router.get_git_drift()

    assert result["is_drifted"] is None
    assert result["drift_details"] == "unknown (no git checkout; see /api/deployment/drift)"


@pytest.mark.asyncio
async def test_git_drift_reports_up_to_date_when_both_commits_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing behaviour is preserved when both commits are present and equal."""
    monkeypatch.setattr(deployment_router.subprocess, "run", _fake_run_both_present_same)

    result = await deployment_router.get_git_drift()

    assert result["is_drifted"] is False
    assert result["drift_details"] == "up to date"


@pytest.mark.asyncio
async def test_git_drift_reports_drifted_when_commits_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing behaviour is preserved when both commits are present and differ."""
    monkeypatch.setattr(deployment_router.subprocess, "run", _fake_run_both_present_different)

    result = await deployment_router.get_git_drift()

    assert result["is_drifted"] is True
    assert result["drift_details"] == "deployed version differs from origin/main"
