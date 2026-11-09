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
    author: str = "d-sorggeminiagent[bot]",
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
) -> dict[str, Any]:
    envelope = {
        "version": 1,
        "id": f"msg-{comment_id}",
        "kind": "release",
        "session": session,
        "repo": repo,
        "at": "2026-10-29T12:00:00+00:00",
    }
    body = f"<!-- agent-msg v1 id=msg-{comment_id} kind=release -->\n```json agent-msg\n{json.dumps(envelope)}\n```"
    return {"id": comment_id, "body": body, "user": {"login": author}}


@pytest.mark.unit
def test_paginate_board_comments_enforces_first_owner_binding_and_separates_historical_vs_active_conflicts() -> None:
    # 1. Expired session with cross-repo event: historical warning only, complete=True
    comments_hist = [
        _presence_comment(1, session="expired-s", repo="Tools", expires="2020-01-01T00:00:00+00:00"),
        _release_comment(2, session="expired-s", repo="Runner_Dashboard"),
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


def _env_dict(cid: int, sess: str, **kw: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "id": f"msg-{cid}",
        "kind": kw.get("kind", "presence"),
        "agent": "codex",
        "session": sess,
        "repo": kw.get("repo", "Runner_Dashboard"),
        "at": "2026-10-29T12:00:00+00:00",
        "expires": kw.get("expires", "2099-01-01T00:00:00+00:00"),
        **kw.get("extra", {}),
    }


@pytest.mark.unit
def test_quoted_prose_comment_is_ignored_as_non_coordination() -> None:
    bj = json.dumps(_env_dict(1, "s-q"))
    prose = {"id": 1, "body": f"Note:\n<!-- agent-message v1 -->\n```json\n{bj}\n```\nIgnore.", "user": {"login": "r"}}
    assert paginate_board_comments(lambda p, n: [prose])["sessions"] == []
    cj = json.dumps(_env_dict(2, "s-c"))
    body = f"<!-- agent-message v1 -->\n```json\n{cj}\n```\n<!-- fleet-CTA-footer -->\nx"
    canon = {"id": 2, "body": body, "user": {"login": "b"}}
    res = paginate_board_comments(lambda p, n: [canon])
    assert res["complete"] is True and [s["session"] for s in res["sessions"]] == ["s-c"]


@pytest.mark.unit
def test_strict_author_binding_and_cross_author_spoofing() -> None:
    anon_body = f"<!-- agent-message v1 -->\n```json\n{json.dumps(_env_dict(1, 's-anon'))}\n```"
    res_anon = paginate_board_comments(lambda p, n: [{"id": 1, "body": anon_body, "user": {}}])
    assert not res_anon["complete"] and any(
        "without verified GitHub user.login" in w for w in res_anon["read_warnings"]
    )

    c1 = _presence_comment(10, session="s-spoof", repo="Runner_Dashboard", author="userA")
    c2 = _release_comment(11, session="s-spoof", repo="Runner_Dashboard", author="userB")
    res_sp = paginate_board_comments(lambda p, n: [c1, c2])
    assert not res_sp["complete"] and "Rejected identity change for s-spoof" in res_sp["active_conflicts"]


@pytest.mark.unit
def test_malformed_coordination_block_and_invalid_expires_fail_closed() -> None:
    bad_j = {"id": 1, "body": "<!-- agent-message v1 -->\n```json\n{broken\n```", "user": {"login": "u"}}
    assert not paginate_board_comments(lambda p, n: [bad_j])["complete"]

    no_exp_b = f"<!-- agent-message v1 -->\n```json\n{json.dumps(_env_dict(2, 's-ne', expires=''))}\n```"
    assert not paginate_board_comments(lambda p, n: [{"id": 2, "body": no_exp_b, "user": {"login": "u"}}])["complete"]

    bad_r_b = f"<!-- agent-message v1 -->\n```json\n{json.dumps(_env_dict(3, 's-nr', repo=''))}\n```"
    assert not paginate_board_comments(lambda p, n: [{"id": 3, "body": bad_r_b, "user": {"login": "u"}}])["complete"]

    dup = _presence_comment(4, session="s-dup", author="u")
    assert len(paginate_board_comments(lambda p, n: [dup, dict(dup, id=5)])["sessions"]) == 1


@pytest.mark.unit
def test_parse_read_preserves_pre_pagination_diagnostics_and_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(board_mod, "board_page_fetcher", lambda p, n: [_presence_comment(1, session="pag-s")])
    raw_dict = {
        "available": True,
        "complete": False,
        "comment_limit_exceeded": True,
        "read_warnings": ["pre-rw"],
        "replay_warnings": ["pre-rep"],
        "active_conflicts": ["pre-act"],
        "sessions": [{"session": "trunc-s", "repo": "RD"}],
    }
    raw = ScriptResult(0, json.dumps(raw_dict), "")
    p = board_mod._parse_read(raw)
    assert "pre-rw" in p["read_warnings"] and "pre-rep" in p["replay_warnings"] and "pre-act" in p["active_conflicts"]
    assert p["comment_limit_exceeded"] is False and [s["session"] for s in p["sessions"]] == ["pag-s"]

    conflict_page = [
        _presence_comment(10, session="live-s", author="u1"),
        _release_comment(11, session="live-s", author="u2", repo="Runner_Dashboard"),
    ]
    monkeypatch.setattr(board_mod, "board_page_fetcher", lambda p, n: conflict_page)
    p_conf = board_mod._parse_read(raw)
    assert not p_conf["comment_limit_exceeded"] and not p_conf["complete"] and len(p_conf["sessions"]) == 1

    monkeypatch.setattr(board_mod, "board_page_fetcher", lambda p, n: (_ for _ in ()).throw(RuntimeError("fail")))
    assert board_mod._parse_read(raw)["comment_limit_exceeded"] is True


@pytest.mark.unit
def test_coordination_files_and_functions_size_caps() -> None:
    for name in ("board.py", "board_pagination.py"):
        p = Path(f"backend/coordination/{name}")
        assert p.exists() and len(p.read_text(encoding="utf-8").splitlines()) < 400
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = (node.end_lineno or node.lineno) - node.lineno + 1
                assert length <= 50, f"{name}:{node.name} exceeds 50 lines ({length})"
