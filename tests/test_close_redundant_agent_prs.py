"""Tests for scripts.close_redundant_agent_prs (#2895)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.close_redundant_agent_prs import (  # noqa: E402
    CloserStats,
    PullView,
    _pick_winner,
    pr_closing_issue_numbers,
    require_github_token,
    run_closer,
)


def test_closing_keywords_ignore_refs() -> None:
    body = "Refs #9\nCloses #10\nFixes org/repo#11"
    assert pr_closing_issue_numbers("", body) == frozenset({10, 11})
    assert pr_closing_issue_numbers("Refs #5", "Refs #5 again") == frozenset()


def test_anti_phantom_refs_pr_not_redundant_with_closes_pr() -> None:
    stats = run_closer(
        "org/Runner_Dashboard",
        pulls=[_pr(1, "Refs #42", "codex"), _pr(2, "Closes #42", "jules")],
        dry_run=True,
        agent_filter=None,
        close_pr=lambda *_: None,
        comment=lambda *_: None,
    )
    assert stats.closed == 0 and stats.examined == 2


def test_duplicate_closers_resolve_by_priority() -> None:
    closed: list[int] = []
    stats = run_closer(
        "org/Runner_Dashboard",
        pulls=[_pr(10, "Closes #99", "claude"), _pr(11, "Closes #99", "jules")],
        dry_run=False,
        agent_filter=None,
        close_pr=lambda _repo, num: closed.append(num),
        comment=lambda *_: None,
    )
    assert closed == [11] and stats.closed == 1


def test_user_pr_wins_over_agent() -> None:
    closed: list[int] = []
    run_closer(
        "org/Runner_Dashboard",
        pulls=[_pr(1, "Closes #1", "user"), _pr(2, "Closes #1", "codex")],
        dry_run=False,
        agent_filter=None,
        close_pr=lambda _repo, num: closed.append(num),
        comment=lambda *_: None,
    )
    assert closed == [2]


def test_dry_run_never_closes() -> None:
    closed: list[int] = []
    run_closer(
        "org/Runner_Dashboard",
        pulls=[_pr(1, "Closes #3", "codex"), _pr(2, "Closes #3", "jules")],
        dry_run=True,
        agent_filter=None,
        close_pr=lambda _repo, num: closed.append(num),
        comment=lambda *_: None,
    )
    assert closed == []


def test_require_github_token_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(SystemExit) as exc:
        require_github_token()
    assert exc.value.code == 1


def test_pick_winner_prefers_higher_priority() -> None:
    assert _pick_winner(
        [PullView(1, "t", "Closes #1", "claude", {}), PullView(2, "t", "Closes #1", "jules", {})]
    ).number == 1


def test_closer_stats_json() -> None:
    assert '"examined": 1' in CloserStats(examined=1, closed=0, skipped=0).to_json()


def _pr(number: int, body: str, agent: str) -> dict[str, Any]:
    labels = [{"name": f"agent:{agent}"}] if agent != "user" else []
    login = "human" if agent == "user" else f"{agent}-bot"
    return {
        "number": number,
        "title": "title",
        "body": body,
        "labels": labels,
        "user": {"login": login, "type": "User" if agent == "user" else "Bot"},
        "head": {"ref": f"{agent}/lane"},
    }
