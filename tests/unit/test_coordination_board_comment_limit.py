"""Regression tests for RM #2376 board >1,000 comment-limit handling (issue #3550).

Verifies that:
1. Full pagination reads past 1,000 comments (>10 pages of 100 comments) and
   surfaces presence/claims from comments beyond the old 1,000-comment cap.
2. When the board or claim check cannot be read completely past 1,000 comments
   (``complete=False`` / page-limit truncation), session checks and issue claims
   fail closed (deny claims) instead of failing open.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
    at: str | None = None,
    author: str = "d-sorggeminiagent[bot]",
) -> dict[str, Any]:
    now, is_exp = datetime.now(UTC), expires == "2020-01-01T00:00:00+00:00"
    at_str = at or ("2019-12-31T20:00:00Z" if is_exp else (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
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
        "goals": {},
        "at": at_str,
        "expires": expires if is_exp else (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    body = f"<!-- agent-msg v1 id=msg-{comment_id} kind=presence -->\n```json agent-msg\n{json.dumps(envelope)}\n```"
    return {"id": comment_id, "body": body, "user": {"login": author}}


def _noise_comment(comment_id: int, author: str = "d-sorggeminiagent[bot]") -> dict[str, Any]:
    return {"id": comment_id, "body": f"status note #{comment_id}", "user": {"login": author}}


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


def _release_comment(
    comment_id: int,
    *,
    session: str,
    repo: str,
    author: str = "d-sorggeminiagent[bot]",
    at: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    envelope = {
        "version": 1,
        "id": f"msg-{comment_id}",
        "kind": "release",
        "session": session,
        "repo": repo,
        "at": at or (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    body = f"<!-- agent-msg v1 id=msg-{comment_id} kind=release -->\n```json agent-msg\n{json.dumps(envelope)}\n```"
    return {"id": comment_id, "body": body, "user": {"login": author}}


@pytest.mark.unit
def test_paginate_board_comments_enforces_first_owner_binding_and_separates_historical_vs_active_conflicts() -> None:
    # 1. Expired session with cross-repo event: historical warning only, complete=True
    comments_hist = [
        _presence_comment(1, session="expired-s", repo="Tools", expires="2020-01-01T00:00:00+00:00"),
        _release_comment(2, session="expired-s", repo="Runner_Dashboard", at="2020-01-01T01:00:00+00:00"),
        _presence_comment(3, session="clean-s", repo="Tools", expires="2099-01-01T00:00:00+00:00"),
    ]
    res_hist = paginate_board_comments(lambda page, per_page: comments_hist)
    assert res_hist["complete"] is True
    assert res_hist["degraded_history"] is True
    assert "Rejected identity change for expired-s" in res_hist["replay_warnings"]
    assert res_hist["active_conflicts"] == []
    assert [s["session"] for s in res_hist["sessions"]] == ["clean-s"]

    # 2. Live session with contested identity takeover: active conflict, complete=False
    comments_contested = [
        _presence_comment(10, session="live-s", repo="Runner_Dashboard", expires="2099-01-01T00:00:00+00:00"),
        _release_comment(11, session="live-s", repo="Tools"),
    ]
    res_contested = paginate_board_comments(lambda page, per_page: comments_contested)
    assert res_contested["complete"] is False
    assert "Rejected identity change for live-s" in res_contested["active_conflicts"]
    assert "Rejected identity change for live-s" in res_contested["replay_warnings"]
    assert [s["session"] for s in res_contested["sessions"]] == ["live-s"]


@pytest.mark.unit
def test_pagination_wiring_conflicts_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    assert board_mod.board_page_fetcher is board_mod.default_board_page_fetcher
    board_mod.reset_cache()
    assert board_mod.board_page_fetcher is board_mod.default_board_page_fetcher
    monkeypatch.setattr("coordination.board_pagination.gh_api_json", lambda u: [_presence_comment(1, session="live-a")])
    d_lim = {"available": True, "complete": False, "comment_limit_exceeded": True, "sessions": [], "warnings": [_PAGE_LIMIT_WARNING, "Rejected identity change for old-dead"]}  # noqa: E501  # fmt: skip
    p_lim = board_mod._parse_read(ScriptResult(0, json.dumps(d_lim), ""))
    assert p_lim["complete"] is True and p_lim["degraded_history"] is True and p_lim["read_warnings"] == []
    assert "Rejected identity change for old-dead" in p_lim["replay_warnings"]

    d_leg = {"available": True, "complete": True, "sessions": [{"session": "live-b", "repo": "RD"}], "warnings": ["Rejected identity change for old-dead", "Rejected identity change for live-b", "bad"]}  # noqa: E501  # fmt: skip
    p_leg = board_mod._parse_read(ScriptResult(0, json.dumps(d_leg), ""))
    assert not p_leg["complete"] and p_leg["active_conflicts"] == ["Rejected identity change for live-b"]
    assert "bad" in p_leg["read_warnings"] and "Rejected identity change for old-dead" in p_leg["replay_warnings"]

    c1, c2 = _presence_comment(1, session="s1", author="u"), _presence_comment(2, session="s2", author="u2")
    m_env = {"version": 1, "id": "m1", "kind": "message", "session": "s1", "repo": "Runner_Dashboard", "recipient": "s2", "text": "hold", "at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}  # noqa: E501  # fmt: skip
    c3 = {"id": 11, "user": {"login": "u"}, "body": f"<!-- agent-message v1 -->\n```json\n{json.dumps(m_env)}\n```"}
    monkeypatch.setattr(board_mod, "board_page_fetcher", lambda p, n: [c1, c2, c3])
    p_inb = board_mod._parse_read(ScriptResult(0, json.dumps(d_lim), ""), session="s2")
    assert p_inb["complete"] is True and len(p_inb["messages"]) == 1 and len(p_inb["conflicts"]) == 1

    at_s, exp_s = now.strftime("%Y-%m-%dT%H:%M:%SZ"), (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    bb = {"version": 1, "id": "m", "kind": "presence", "agent": "a", "session": "s", "repo": "RD", "issue": 3550, "branch": "b", "paths": ["p"], "at": at_s, "expires": exp_s}  # noqa: E501  # fmt: skip
    for bp in ({**bb, "version": 2}, {**bb, "issue": "3550"}, {**bb, "kind": "bogus"}, {**bb, "paths": ["../b"]}, {**bb, "expires": (now + timedelta(hours=9)).isoformat()}):  # noqa: E501  # fmt: skip
        b = f"<!-- agent-message v1 -->\n```json\n{json.dumps(bp)}\n```"
        assert not paginate_board_comments(lambda p, n, b=b: [{"id": 1, "body": b, "user": {"login": "u"}}])["complete"]
    assert not paginate_board_comments(lambda p, n: [{"id": 1, "body": "<!-- agent-message v1 -->\n```json\n{}\n```", "user": {}}])["complete"]  # noqa: E501  # fmt: skip

    for name in ("board.py", "board_pagination.py"):
        pth = Path(f"backend/coordination/{name}")
        assert pth.exists() and len(pth.read_text(encoding="utf-8").splitlines()) < 380
        assert all((n.end_lineno or n.lineno) - n.lineno + 1 <= 45 for n in ast.walk(ast.parse(pth.read_text(encoding="utf-8"), filename=str(pth))) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))  # noqa: E501  # fmt: skip
