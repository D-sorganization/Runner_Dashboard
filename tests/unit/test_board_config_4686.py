"""Board routing follows RM config and fails closed when that config is absent (#4686)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from coordination.board_pagination import board_config, default_board_page_fetcher, paginate_board_comments
from coordination.postman_runner import GitHubBoardSource, ReadOnlyBoard, run_dry_cycle
from coordination.rm_scripts import ScriptResult

NOW = datetime(2026, 11, 26, 21, 0, tzinfo=UTC)
STALE_BOARDS = {1576, 2376, 2539}


def _write_config(root: Path, issue: int, preds: list[int] | None = None) -> None:
    cfg = root / "config" / "agent_communication.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps(
            {
                "board_repo": "D-sorganization/Repository_Management",
                "board_issue": issue,
                "board_window_days": 3,
                "board_predecessors": preds if preds is not None else [],
            }
        ),
        encoding="utf-8",
    )


def _source(configured) -> GitHubBoardSource:
    return GitHubBoardSource(
        run=lambda _argv: ScriptResult(0, json.dumps({"state": "open", "comments": 1}), ""),
        configured=configured,
    )


def _sessions() -> dict:
    return {
        "available": True,
        "sessions": [{"session": "claude-1", "agent": "claude", "repo": "Repository_Management"}],
    }


def _board() -> ReadOnlyBoard:
    return ReadOnlyBoard(sessions_reader=_sessions, inbox_reader=lambda _s, _r: {"messages": [], "warnings": []})


@pytest.mark.unit
def test_configured_routing_uses_the_live_board_issue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path, 3089, preds=[2851])
    monkeypatch.setattr("coordination.board_pagination.rm_root", lambda: tmp_path)
    monkeypatch.setattr("coordination.postman_runner.rm_root", lambda: tmp_path)

    repo, issue, days, preds = board_config()
    assert (repo, issue, days, preds) == ("D-sorganization/Repository_Management", 3089, 3, [2851])

    entry = run_dry_cycle(
        board_port=_board(),
        source=_source(lambda: 3089),
        roster_ids=("claude",),
        directory=tmp_path,
        clock=lambda: NOW,
    )
    assert entry["board_issue"] == 3089
    assert entry["board_issue"] not in STALE_BOARDS
    assert "error" not in entry


@pytest.mark.unit
def test_rotation_follows_a_new_configured_board_number(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path, 3089, preds=[2851])
    monkeypatch.setattr("coordination.postman_runner.rm_root", lambda: tmp_path)
    (tmp_path / "postman_state.json").write_text(
        json.dumps({"board_issue": 2851, "delivered": ["presence:pm-claude"]}),
        encoding="utf-8",
    )

    entry = run_dry_cycle(
        board_port=_board(),
        source=_source(lambda: 3089),
        roster_ids=("claude",),
        directory=tmp_path,
        clock=lambda: NOW,
    )
    assert entry["board_issue"] == 3089
    assert entry["board_issue"] not in STALE_BOARDS


@pytest.mark.unit
def test_missing_config_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("coordination.board_pagination.rm_root", lambda: None)
    monkeypatch.setattr("coordination.postman_runner.rm_root", lambda: None)
    monkeypatch.setattr(
        "coordination.board_pagination.gh_api_json",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("missing config must not query GitHub")),
    )

    with pytest.raises(Exception, match="agent_communication.json is absent"):
        board_config()

    page = paginate_board_comments(default_board_page_fetcher)
    assert page["available"] is False
    assert "absent" in str(page.get("reason") or page.get("error") or "")
    assert "2539" not in json.dumps(page)

    entry = run_dry_cycle(directory=tmp_path, clock=lambda: NOW)
    assert entry.get("available") is False
    assert entry.get("board_issue") not in STALE_BOARDS
    assert "absent" in entry["error"]
