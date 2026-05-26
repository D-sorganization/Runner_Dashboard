"""Regression tests for GitHub status banner state."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from routers import diagnostics as diagnostics_router  # noqa: E402


@pytest.mark.asyncio
async def test_github_status_uses_health_summary_when_client_status_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """The header must not show GitHub unknown after /api/health proved connectivity."""

    class FakeGhClient:
        @staticmethod
        def get_status() -> dict:
            return {
                "status": "unknown",
                "detail": "No GitHub API request has completed in this process.",
                "endpoint": "",
                "retry_after_seconds": 0,
                "updated_at": None,
            }

    async def fake_health_summary(org: str) -> dict:  # noqa: ARG001
        return {
            "github_api": "connected",
            "timestamp": "2026-05-26T17:00:00+00:00",
            "runners_registered": 30,
        }

    monkeypatch.setitem(sys.modules, "gh_client", FakeGhClient)
    import gh_utils

    monkeypatch.setattr(gh_utils, "get_rate_limit_status", lambda: {"status": "ok"})
    monkeypatch.setattr(gh_utils, "get_gh_health_summary", fake_health_summary)

    result = await diagnostics_router.get_github_status()

    assert result["status"] == "ok"
    assert result["source"] == "health_cache"


@pytest.mark.asyncio
async def test_github_status_preserves_rate_limit_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    import gh_utils

    monkeypatch.setattr(
        gh_utils,
        "get_rate_limit_status",
        lambda: {"status": "rate_limited", "retry_after_seconds": 30},
    )

    result = await diagnostics_router.get_github_status()

    assert result["status"] == "rate_limited"
    assert result["source"] == "rate_limit"
