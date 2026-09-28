"""Work-request dispatchers resolve the target repository without ``server``.

``_dispatch_remediation_workflow`` and ``_dispatch_issue_or_pr_action`` did
``from server import _normalize_repository_input``; that helper left
``server.py`` in #941, so every approved remediation, issue or PR work request
failed with ImportError. Found by resolving cross-module imports in mypy (#1734).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from staff import work_request_dispatch as wrd


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _record(endpoint: str, payload: dict[str, Any], prefix: str = "") -> None:  # noqa: ARG001
        calls.append((endpoint, payload))

    monkeypatch.setattr(wrd, "_dispatch_workflow_json", _record)
    return calls


@pytest.mark.unit
def test_remediation_dispatch_targets_the_full_repository(sent: list[tuple[str, dict[str, Any]]]) -> None:
    result = asyncio.run(wrd._dispatch_remediation_workflow("Runner_Dashboard", 7, None, "fix it"))

    assert result["target_repository"].endswith("/Runner_Dashboard")
    assert sent[0][1]["inputs"]["target_repository"] == result["target_repository"]


@pytest.mark.unit
def test_issue_dispatch_targets_the_full_repository(sent: list[tuple[str, dict[str, Any]]]) -> None:
    result = asyncio.run(wrd._dispatch_issue_or_pr_action("issue", "Runner_Dashboard", 12, None, None, "do it"))

    assert result["workflow"] == "Agent-Issue-Dispatch.yml"
    assert result["target_repository"].endswith("/Runner_Dashboard")
    assert sent[0][1]["inputs"]["number"] == "12"
