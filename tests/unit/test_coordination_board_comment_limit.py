"""Regression tests for RM #2376 board >1,000 comment-limit handling (issue #3550).

Verifies that:
1. Full pagination reads past 1,000 comments (>10 pages of 100 comments) and
   surfaces presence/claims from comments beyond the old 1,000-comment cap.
2. When the board or claim check cannot be read completely past 1,000 comments
   (``complete=False`` / page-limit truncation), issue claims fail closed (deny
   claims) instead of failing open. Incomplete history is not live presence
   (#4569). Pagination failures, unclassified incompleteness, and conflicts
   stay fail-closed.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from coordination import board as board_mod
from coordination import claims as claims_mod
from coordination.board import BOARD_COMMENT_LIMIT, RMScriptError, paginate_board_comments
from coordination.board_pagination import (
    BOARD_PREDECESSOR_MAX_PAGES,
    MAX_EVENT_BYTES,
    _merge_paginated,
    _paginate_pages,
    _parse_comment_envelope,
    board_config,
    default_board_page_fetcher,
)
from coordination.claims import ClaimHeldError
from coordination.gh_cli import gh_api_json
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
    """When >1,000 comments cannot be read completely, claims and absent-session presence fail closed (#4569)."""
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

    # Pagination died on a rate limit, so the read is not a classified comment-limit skip.
    # Absence is not presence, and this failure stays fail-closed (#3550, #4569).
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

    # 3. Same-repo release frees the session to register in another repo (#4624)
    comments_switch = [
        _presence_comment(20, session="switch-s", repo="Runner_Dashboard", expires="2099-01-01T00:00:00+00:00"),
        _release_comment(21, session="switch-s", repo="Runner_Dashboard"),
        _presence_comment(22, session="switch-s", repo="Tools", expires="2099-01-01T00:00:00+00:00"),
    ]
    res_switch = paginate_board_comments(lambda page, per_page: comments_switch)
    assert res_switch["complete"] is True
    assert res_switch["active_conflicts"] == []
    assert [s["session"] for s in res_switch["sessions"]] == ["switch-s"]
    assert res_switch["sessions"][0]["repo"] == "Tools"


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
        pth = Path(__file__).resolve().parents[2] / "backend" / "coordination" / name
        assert pth.exists() and len(pth.read_text(encoding="utf-8").splitlines()) < 380
        assert all((n.end_lineno or n.lineno) - n.lineno + 1 <= 45 for n in ast.walk(ast.parse(pth.read_text(encoding="utf-8"), filename=str(pth))) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))  # noqa: E501  # fmt: skip


@pytest.mark.unit
def test_merge_paginated_falsy_fallback_handling() -> None:
    data = {
        "messages": [{"id": "stale_m", "text": "stale"}],
        "conflicts": [{"session": "stale_s"}],
        "comment_count": 500,
    }
    pag = {
        "pages_complete": True,
        "complete": True,
        "messages": [],
        "conflicts": [],
        "sessions": [],
    }
    merged = _merge_paginated(data, pag)
    assert merged["messages"] == []
    assert merged["conflicts"] == []

    pag_undone = {"pages_complete": False, "complete": False, "messages": [], "conflicts": []}
    merged_undone = _merge_paginated(data, pag_undone)
    assert len(merged_undone["messages"]) == 1
    assert len(merged_undone["conflicts"]) == 1


@pytest.mark.unit
@pytest.mark.unit
def test_explicit_release_wins_over_leftover_presence_for_claim_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A release after presence must clear the hold even when earlier comments remain on the board."""
    from coordination import claims

    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    at_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    exp_str = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    presence = {
        "version": 1,
        "id": "p1",
        "kind": "presence",
        "agent": "claude",
        "session": "s1",
        "repo": "Runner_Dashboard",
        "issue": 4574,
        "branch": "feat/board-hygiene",
        "paths": ["backend/coordination/claims.py"],
        "goals": {"task": "repair"},
        "at": at_str,
        "expires": exp_str,
    }
    release = {"version": 1, "id": "r1", "kind": "release", "session": "s1", "repo": "Runner_Dashboard", "at": at_str}
    comments = [
        {
            "id": 1,
            "user": {"login": "legit"},
            "body": f"<!-- agent-message v1 -->\n```json\n{json.dumps(presence)}\n```",
        },
        {
            "id": 2,
            "user": {"login": "legit"},
            "body": f"<!-- agent-message v1 -->\n```json\n{json.dumps(release)}\n```",
        },
    ]
    board_state = paginate_board_comments(lambda p, n: comments if p == 1 else [], now=now)
    assert board_state["sessions"] == []

    monkeypatch.setattr(
        "coordination.claims.board_config", lambda: ("D-sorganization/Repository_Management", 3029, 3, [])
    )
    monkeypatch.setattr(board_mod, "cached_board_state", lambda: board_state)
    assert claims._board_claim_override("Runner_Dashboard", 4574) is None


def test_apply_event_midstream_conflict_not_poisoned_if_released() -> None:
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    at_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    exp_str = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    p1 = {"version": 1, "id": "p1", "kind": "presence", "agent": "claude", "session": "s1", "repo": "Runner_Dashboard", "issue": 100, "branch": "feat", "paths": ["src/a.py"], "goals": {"task": "fix"}, "at": at_str, "expires": exp_str}  # noqa: E501  # fmt: skip
    spoof_msg = {"version": 1, "id": "m1", "kind": "message", "session": "s1", "repo": "Runner_Dashboard", "recipient": "*", "text": "spoof", "at": at_str}  # noqa: E501  # fmt: skip
    rel = {"version": 1, "id": "r1", "kind": "release", "session": "s1", "repo": "Runner_Dashboard", "at": at_str}
    comments = [
        {"id": 1, "user": {"login": "legit"}, "body": f"<!-- agent-message v1 -->\n```json\n{json.dumps(p1)}\n```"},
        {
            "id": 2,
            "user": {"login": "attacker"},
            "body": f"<!-- agent-message v1 -->\n```json\n{json.dumps(spoof_msg)}\n```",
        },  # noqa: E501
        {"id": 3, "user": {"login": "legit"}, "body": f"<!-- agent-message v1 -->\n```json\n{json.dumps(rel)}\n```"},
    ]
    res = paginate_board_comments(lambda p, n: comments if p == 1 else [], now=now)
    assert res["complete"] is True
    assert res["active_conflicts"] == []
    assert "Rejected identity change for s1" in res["replay_warnings"]
    assert res["degraded_history"] is True


@pytest.mark.unit
def test_predecessor_over_page_cap_bounded_read_stays_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retired board #3029 (>1k comments) must not fail-closed hub reads when the TTL window is capped."""
    pred_issue = 3029
    calls: dict[int, int] = {}

    def fake_gh(path: str) -> list[dict[str, Any]]:
        if f"/issues/{pred_issue}/comments" in path:
            page_m = re.search(r"(?:^|[&?])page=(\d+)", path)
            assert page_m is not None
            page = int(page_m.group(1))
            calls[page] = calls.get(page, 0) + 1
            return [_noise_comment((page - 1) * 100 + i, author=f"u{i}") for i in range(1, 101)]
        if "/issues/3037/comments" in path:
            return [_presence_comment(1, session="on-new-board", issue=2657)]
        raise AssertionError(path)

    monkeypatch.setattr("coordination.board_pagination.board_config", lambda: ("repo", 3037, 3, [pred_issue]))
    monkeypatch.setattr("coordination.board_pagination.gh_api_json", fake_gh)

    now = datetime.now(UTC)
    # Page 0 is only scheduled when ``fetch_page`` is ``default_board_page_fetcher`` (identity).
    result = paginate_board_comments(default_board_page_fetcher, per_page=100, max_pages=10, now=now)
    assert result["available"] is True
    assert result["complete"] is True
    assert any("Board #3029" in w and "predecessor read capped" in w for w in result["replay_warnings"])
    assert not any("Board #3029" in w for w in result["read_warnings"])
    assert calls.get(BOARD_PREDECESSOR_MAX_PAGES) == 1
    assert BOARD_PREDECESSOR_MAX_PAGES not in calls or calls.get(BOARD_PREDECESSOR_MAX_PAGES + 1) is None


@pytest.mark.unit
def test_current_board_page_cap_still_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Incomplete pagination on the active board must still fail closed (#3550)."""
    comments = [_noise_comment(i) for i in range(1, 1001)]

    def fetch_page(page: int, per_page: int) -> list[dict[str, Any]]:
        if page == 0:
            return []
        start = (page - 1) * per_page
        return comments[start : start + per_page]

    monkeypatch.setattr("coordination.board_pagination.board_config", lambda: ("repo", 3037, 3, []))
    result = paginate_board_comments(fetch_page, per_page=100, max_pages=10)
    assert result["complete"] is False
    assert result["comment_limit_exceeded"] is True


@pytest.mark.unit
def test_board_config_and_page_zero_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config" / "agent_communication.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("not json", encoding="utf-8")
    monkeypatch.setattr("coordination.board_pagination.rm_root", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="Invalid agent_communication.json"):
        board_config()

    cfg.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Invalid agent_communication.json"):
        board_config()

    from coordination.board_pagination import default_board_page_fetcher

    monkeypatch.setattr("coordination.board_pagination.board_config", lambda: ("repo", 1, 3, [100]))
    monkeypatch.setattr(
        "coordination.board_pagination._read_predecessor_comments",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("predecessor fetch failed")),
    )  # noqa: E501
    st: dict[str, Any] = {
        "active": {},
        "owners": {},
        "messages": [],
        "acks": set(),
        "replay_w": [],
        "live_c": [],
        "read_w": [],
        "rejected": {},
    }  # noqa: E501
    done, total = _paginate_pages(default_board_page_fetcher, 100, 100, datetime.now(UTC), st)
    assert done is False
    assert any("page 0: predecessor fetch failed" in w for w in st["read_w"])

    monkeypatch.setattr("coordination.board_pagination.gh_api_json", lambda p: "not a list")
    st2: dict[str, Any] = {
        "active": {},
        "owners": {},
        "messages": [],
        "acks": set(),
        "replay_w": [],
        "live_c": [],
        "read_w": [],
        "rejected": {},
    }  # noqa: E501
    done2, total2 = _paginate_pages(default_board_page_fetcher, 100, 100, datetime.now(UTC), st2)
    assert done2 is False
    assert any("page 0" in w for w in st2["read_w"])

    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, stdout="   \n", stderr="")
    )  # noqa: E501
    with pytest.raises(RuntimeError, match="returned empty stdout"):
        gh_api_json("/repos/test")


@pytest.mark.unit
def test_envelope_framing_and_goals_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    at_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    exp_str = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    base_env = {"version": 1, "id": "p1", "kind": "presence", "agent": "claude", "session": "s1", "repo": "Runner_Dashboard", "issue": 100, "branch": "feat", "paths": ["src/a.py"], "goals": {"task": "fix"}, "at": at_str, "expires": exp_str}  # noqa: E501  # fmt: skip

    raw_ws = "   <!-- agent-message v1 -->\n```json\n" + json.dumps(base_env) + "\n```"
    c_ws = {"id": 1, "user": {"login": "u"}, "body": raw_ws}
    env, author, err = _parse_comment_envelope(c_ws)
    assert env is None and author == "u" and err == "Malformed coordination envelope"

    big_raw = "<!-- agent-message v1 -->\n```json\n" + json.dumps(base_env) + (" " * (MAX_EVENT_BYTES + 10)) + "\n```"
    c_big = {"id": 2, "user": {"login": "u"}, "body": big_raw}
    env, author, err = _parse_comment_envelope(c_big)
    assert env is None and author == "u" and err == "Malformed coordination envelope"

    no_goals = {**base_env, "goals": None}
    raw_ng = "<!-- agent-message v1 -->\n```json\n" + json.dumps(no_goals) + "\n```"
    c_ng = {"id": 3, "user": {"login": "u"}, "body": raw_ng}
    env, author, err = _parse_comment_envelope(c_ng)
    assert env is None and err is not None and "Goals must map bounded keys to bounded outcomes" in err

    junk_body = "<!-- agent-message v1 -->\n```json\n" + json.dumps(base_env) + "\n```\n---\n_Generated by [Claude Code](https://claude.ai/code)_\nextra trailing content"  # noqa: E501  # fmt: skip
    c_junk = {"id": 4, "user": {"login": "u"}, "body": junk_body}
    env, author, err = _parse_comment_envelope(c_junk)
    assert env is None and author == "u" and err == "Malformed coordination envelope"

    from coordination import claims

    monkeypatch.setattr(
        "coordination.claims.board_config", lambda: ("D-sorganization/Repository_Management", 9999, 3, [])
    )  # noqa: E501
    monkeypatch.setattr(
        board_mod,
        "cached_board_state",
        lambda: {
            "available": True,
            "complete": False,
            "comment_limit_exceeded": True,
            "warnings": ["RM #9999 comment limit exceeded"],
        },
    )
    res = claims._board_claim_override("Tools", 1)
    assert res is not None
    assert "RM #9999" in res["reason"]


@pytest.mark.unit
def test_board_claim_override_status_only_and_blocked_heartbeats_do_not_reserve_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Status-only / blocked heartbeats must not reserve work, while real claims and incomplete history still block."""
    from coordination import claims

    # Configured board is 3029; BOARD_ISSUE in board_mod should not override to 2539
    monkeypatch.setattr(
        "coordination.claims.board_config", lambda: ("D-sorganization/Repository_Management", 3029, 3, [2539, 2851])
    )

    # 1. Blocked heartbeat: goals.state == "blocked"
    blocked_session = {
        "repo": "Runner_Dashboard",
        "issue": 4574,
        "agent": "codex",
        "session": "codex-desk-1",
        "branch": "fix/4574",
        "paths": ["backend/coordination/claims.py"],
        "goals": {"state": "blocked", "blocker": "waiting on upstream"},
        "expires": "2099-01-01T00:00:00Z",
    }
    monkeypatch.setattr(
        board_mod,
        "cached_board_state",
        lambda: {
            "available": True,
            "complete": True,
            "sessions": [blocked_session],
            "warnings": [],
        },
    )
    res = claims._board_claim_override("Runner_Dashboard", 4574)
    assert res is None, "Blocked heartbeat must not reserve work"

    # 2. Status-only heartbeat: empty paths and branch == "none"
    status_none_session = {
        "repo": "Runner_Dashboard",
        "issue": 4574,
        "agent": "codex",
        "session": "codex-desk-1",
        "branch": "none",
        "paths": [],
        "goals": {"task": "status check"},
        "expires": "2099-01-01T00:00:00Z",
    }
    monkeypatch.setattr(
        board_mod,
        "cached_board_state",
        lambda: {
            "available": True,
            "complete": True,
            "sessions": [status_none_session],
            "warnings": [],
        },
    )
    res = claims._board_claim_override("Runner_Dashboard", 4574)
    assert res is None, "Empty paths and branch 'none' must not reserve work"

    # 3. Status-only heartbeat: empty paths and branch == ""
    status_empty_session = {
        "repo": "Runner_Dashboard",
        "issue": 4574,
        "agent": "codex",
        "session": "codex-desk-1",
        "branch": "",
        "paths": [],
        "goals": {},
        "expires": "2099-01-01T00:00:00Z",
    }
    monkeypatch.setattr(
        board_mod,
        "cached_board_state",
        lambda: {
            "available": True,
            "complete": True,
            "sessions": [status_empty_session],
            "warnings": [],
        },
    )
    res = claims._board_claim_override("Runner_Dashboard", 4574)
    assert res is None, "Empty paths and empty branch must not reserve work"

    # 4. Real active claim: has paths and branch, not blocked
    real_session = {
        "repo": "Runner_Dashboard",
        "issue": 4574,
        "agent": "antigravity",
        "session": "agy-desk-1",
        "branch": "fix/4574-board-hygiene",
        "paths": ["backend/coordination/claims.py"],
        "goals": {"task": "implement fix"},
        "expires": "2099-01-01T00:00:00Z",
    }
    monkeypatch.setattr(
        board_mod,
        "cached_board_state",
        lambda: {
            "available": True,
            "complete": True,
            "sessions": [real_session],
            "warnings": [],
        },
    )
    res = claims._board_claim_override("Runner_Dashboard", 4574)
    assert res is not None
    assert res["held"] is True
    assert res["agent"] == "antigravity"
    assert "RM #3029" in res["reason"], f"Must use configured board 3029, got {res['reason']}"
    assert "2539" not in res["reason"]

    # 5. Incomplete board history (comment limit exceeded) must still block
    monkeypatch.setattr(
        board_mod,
        "cached_board_state",
        lambda: {
            "available": True,
            "complete": False,
            "comment_limit_exceeded": True,
            "sessions": [],
            "warnings": ["RM #3029 comment limit exceeded"],
        },
    )
    res = claims._board_claim_override("Runner_Dashboard", 4574)
    assert res is not None
    assert res["available"] is False
    assert res["held"] is None
    assert "incomplete" in res["reason"]


@pytest.mark.unit
def test_board_claim_override_completed_handoff_without_paths_clears_custody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Departing handoff status with a branch and no work paths must clear custody without clearing active claims."""
    from coordination import claims

    monkeypatch.setattr(
        "coordination.claims.board_config", lambda: ("D-sorganization/Repository_Management", 3037, 3, [3029])
    )

    handoff_session = {
        "repo": "Runner_Dashboard",
        "issue": 4576,
        "agent": "codex",
        "session": "codex-cli-oglaptop-20261119-181333",
        "branch": "codex-oglaptop-2/4576-runner-safety",
        "paths": [],
        "goals": {"status": "partial handoff"},
        "expires": "2026-11-20T04:19:42.135878+00:00",
    }
    monkeypatch.setattr(
        board_mod,
        "cached_board_state",
        lambda: {"available": True, "complete": True, "sessions": [handoff_session], "warnings": []},
    )
    assert claims._board_claim_override("Runner_Dashboard", 4576) is None

    active_session = {
        "repo": "Runner_Dashboard",
        "issue": 4576,
        "agent": "codex",
        "session": "codex-cli-oglaptop-20261119-190000",
        "branch": "codex-oglaptop-2/4576-runner-safety",
        "paths": ["backend/coordination/claims.py"],
        "goals": {"status": "implementing remaining safety criteria"},
        "expires": "2026-11-20T05:00:00Z",
    }
    monkeypatch.setattr(
        board_mod,
        "cached_board_state",
        lambda: {"available": True, "complete": True, "sessions": [active_session], "warnings": []},
    )
    res = claims._board_claim_override("Runner_Dashboard", 4576)
    assert res is not None and res["held"] is True and res["session"] == "codex-cli-oglaptop-20261119-190000"

    # Active session mentioning handoff in status or prose must remain held when paths exist
    active_handoff_prose_session = {
        "repo": "Runner_Dashboard",
        "issue": 4576,
        "agent": "codex",
        "session": "codex-cli-oglaptop-20261119-191000",
        "branch": "fix/4576-safety",
        "paths": ["backend/coordination/claims.py"],
        "goals": {"state": "active", "status": "implementing handoff validation"},
        "expires": "2026-11-20T05:00:00Z",
    }
    monkeypatch.setattr(
        board_mod,
        "cached_board_state",
        lambda: {"available": True, "complete": True, "sessions": [active_handoff_prose_session], "warnings": []},
    )
    res = claims._board_claim_override("Runner_Dashboard", 4576)
    assert res is not None and res["held"] is True and res["session"] == "codex-cli-oglaptop-20261119-191000"

    # Active session with handoff pending acknowledgement must also remain held
    active_pending_ack_session = {
        "repo": "Runner_Dashboard",
        "issue": 4576,
        "agent": "codex",
        "session": "codex-cli-oglaptop-20261119-192000",
        "branch": "fix/4576-safety",
        "paths": ["backend/coordination/claims.py"],
        "goals": {"status": "handoff pending acknowledgement"},
        "expires": "2026-11-20T05:00:00Z",
    }
    monkeypatch.setattr(
        board_mod,
        "cached_board_state",
        lambda: {"available": True, "complete": True, "sessions": [active_pending_ack_session], "warnings": []},
    )
    res = claims._board_claim_override("Runner_Dashboard", 4576)
    assert res is not None and res["held"] is True and res["session"] == "codex-cli-oglaptop-20261119-192000"
