"""Regression tests for RM #2376 board >1,000 comment-limit handling (issue #3550).

Verifies that:
1. Full pagination reads past 1,000 comments (>10 pages of 100 comments) and
   surfaces presence/claims from comments beyond the old 1,000-comment cap.
2. When the board or claim check cannot be read completely past 1,000 comments
   (``complete=False`` / page-limit truncation), session checks and issue claims
   fail closed (deny claims) instead of failing open.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from coordination import board as board_mod
from coordination import claims as claims_mod
from coordination.board import BOARD_COMMENT_LIMIT, RMScriptError, paginate_board_comments
from coordination.claims import ClaimHeldError
from coordination.rm_scripts import ScriptResult


def _presence_comment(
    comment_id: int,
    *,
    session: str,
    agent: str = "codex",
    repo: str = "Runner_Dashboard",
    issue: int | None = 3550,
    branch: str = "fix/3550",
    expires: str = "2099-01-01T00:00:00+00:00",
) -> dict[str, Any]:
    envelope = {
        "version": 1,
        "id": f"msg-{comment_id}",
        "kind": "presence",
        "agent": agent,
        "session": session,
        "repo": repo,
        "issue": issue,
        "branch": branch,
        "paths": ["backend/coordination/board.py"],
        "at": "2026-10-29T12:00:00+00:00",
        "expires": expires,
    }
    body = f"<!-- agent-msg v1 id=msg-{comment_id} kind=presence -->\n```json agent-msg\n{json.dumps(envelope)}\n```"
    return {"id": comment_id, "body": body}


def _noise_comment(comment_id: int) -> dict[str, Any]:
    return {"id": comment_id, "body": f"status note #{comment_id}"}


_PAGE_LIMIT_WARNING = (
    "Board page limit reached inside the read window; the retrieved view is incomplete, do not rely on absence"
)


@pytest.fixture(autouse=True)
def _clear_coordination_state() -> None:
    board_mod.reset_cache()
    claims_mod.reset_locks()


@pytest.mark.unit
def test_paginate_board_comments_reads_past_1000_comments_completely() -> None:
    """Full pagination across >1,000 mocked comments preserves presence beyond comment #1,000."""
    total = 1055
    assert total > BOARD_COMMENT_LIMIT
    comments = [_noise_comment(i) for i in range(1, total + 1)]
    comments[4] = _presence_comment(5, session="early-session", agent="claude", issue=3501)
    comments[1049] = _presence_comment(1050, session="late-session", agent="codex", issue=3550)

    pages_fetched: list[int] = []

    def fetch_page(page: int, per_page: int) -> list[dict[str, Any]]:
        pages_fetched.append(page)
        start = (page - 1) * per_page
        return comments[start : start + per_page]

    result = paginate_board_comments(fetch_page, per_page=100)
    assert result["available"] is True
    assert result["complete"] is True
    assert result["comment_count"] == total
    assert len(pages_fetched) == 11
    by_session = {s["session"]: s for s in result["sessions"]}
    assert set(by_session) == {"early-session", "late-session"}
    assert by_session["late-session"]["issue"] == 3550


@pytest.mark.unit
def test_board_with_over_1000_comments_paginates_and_blocks_conflicting_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When RM#2376 hits the 10-page cap, board pagination reads >1,000 comments and denies held claim."""
    comments = [_noise_comment(i) for i in range(1, 1051)]
    comments[1045] = _presence_comment(
        1046,
        session="claude-holder",
        agent="claude",
        repo="Runner_Dashboard",
        issue=3550,
    )

    def fetch_page(page: int, per_page: int) -> list[dict[str, Any]]:
        start = (page - 1) * per_page
        return comments[start : start + per_page]

    calls: list[str] = []

    def fake_run_module(module: str, *args: str, **_kwargs: Any) -> ScriptResult:
        del args
        calls.append(module)
        if module == "agent_communicate":
            return ScriptResult(
                0,
                json.dumps(
                    {
                        "available": True,
                        "complete": False,
                        "sessions": [],
                        "warnings": [_PAGE_LIMIT_WARNING],
                    }
                ),
                "",
            )
        if module == "check_agent_claim":
            return ScriptResult(
                0,
                json.dumps(
                    {
                        "repo": "Runner_Dashboard",
                        "issue": 3550,
                        "held": False,
                        "agent": "",
                        "reason": "no claim label",
                    }
                ),
                "",
            )
        raise AssertionError(f"unexpected RM script call: {module}")

    monkeypatch.setattr(board_mod, "run_module", fake_run_module)
    monkeypatch.setattr(claims_mod, "run_module", fake_run_module)
    monkeypatch.setattr(board_mod, "board_page_fetcher", fetch_page)

    status = claims_mod.check("Runner_Dashboard", 3550)
    assert status["available"] is True
    assert status["held"] is True
    assert status["agent"] == "claude"

    with pytest.raises(ClaimHeldError):
        claims_mod.claim(
            "Runner_Dashboard",
            3550,
            agent="codex",
            session="codex-contender",
            intent="attempt conflicting claim",
        )
    assert "post_agent_lease" not in calls


@pytest.mark.unit
def test_incomplete_board_over_1000_comments_denies_claims_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When >1,000 comments on RM#2376 cannot be read completely, claims and session checks fail closed."""
    comments = [_noise_comment(i) for i in range(1, 1101)]

    def truncated_fetch_page(page: int, per_page: int) -> list[dict[str, Any]]:
        if page > 10:
            raise RuntimeError("GitHub API rate limit on page 11")
        start = (page - 1) * per_page
        return comments[start : start + per_page]

    calls: list[str] = []

    def fake_run_module(module: str, *args: str, **_kwargs: Any) -> ScriptResult:
        del args
        calls.append(module)
        if module == "agent_communicate":
            return ScriptResult(
                0,
                json.dumps(
                    {
                        "available": True,
                        "complete": False,
                        "comment_count": 1100,
                        "sessions": [],
                        "warnings": [_PAGE_LIMIT_WARNING],
                    }
                ),
                "",
            )
        if module == "check_agent_claim":
            return ScriptResult(
                0,
                json.dumps(
                    {
                        "repo": "Runner_Dashboard",
                        "issue": 3550,
                        "held": False,
                        "agent": "",
                        "reason": "no claim label",
                    }
                ),
                "",
            )
        raise AssertionError(f"unexpected RM script call: {module}")

    monkeypatch.setattr(board_mod, "run_module", fake_run_module)
    monkeypatch.setattr(claims_mod, "run_module", fake_run_module)
    monkeypatch.setattr(board_mod, "board_page_fetcher", truncated_fetch_page)

    with pytest.raises(RMScriptError):
        board_mod.has_live_session("missing-session", "Runner_Dashboard")

    status = claims_mod.check("Runner_Dashboard", 3550)
    assert status["available"] is False
    assert status["held"] is None

    with pytest.raises(RMScriptError):
        claims_mod.claim(
            "Runner_Dashboard",
            3550,
            agent="codex",
            session="codex-1",
            intent="must be denied when board is incomplete",
        )
    assert "post_agent_lease" not in calls


@pytest.mark.unit
def test_check_agent_claim_incomplete_over_1000_comments_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct check_agent_claim truncation past 1,000 comments fails closed even without a cached board."""

    def fake_run_module(module: str, *args: str, **_kwargs: Any) -> ScriptResult:
        del args
        if module == "check_agent_claim":
            return ScriptResult(
                0,
                json.dumps(
                    {
                        "repo": "Runner_Dashboard",
                        "issue": 3550,
                        "held": False,
                        "complete": False,
                        "comment_count": 1024,
                        "warnings": ["Board page limit reached inside the read window"],
                        "reason": "no claim label",
                    }
                ),
                "",
            )
        raise AssertionError(f"unexpected RM script call: {module}")

    monkeypatch.setattr(claims_mod, "run_module", fake_run_module)

    status = claims_mod.check("Runner_Dashboard", 3550)
    assert status["available"] is False
    assert status["held"] is None

    with pytest.raises(RMScriptError):
        claims_mod.claim(
            "Runner_Dashboard",
            3550,
            agent="codex",
            session="codex-1",
            intent="must be denied",
        )
