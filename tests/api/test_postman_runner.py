"""Postman runner, dry run only (PR #3231 slice 1): it plans deliveries and can write nothing.

Ports are fakes; the GitHub issue check gets a canned ``gh api`` result.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from coordination import postman_runner
from coordination.postman import Delivery
from coordination.postman_runner import (
    BoardUnavailableError,
    DryRunOnlyError,
    GitHubBoardSource,
    ReadOnlyBoard,
    RefusingBus,
)
from coordination.rm_scripts import ScriptResult

NOW = datetime(2026, 10, 27, 23, 0, tzinfo=UTC)


def sessions_ok() -> dict[str, Any]:
    return {
        "available": True,
        "sessions": [{"session": "claude-1", "agent": "claude", "repo": "Repository_Management"}],
    }


def inbox_with(*messages: dict[str, Any]) -> Any:
    return lambda session, repo: {"messages": list(messages), "warnings": []}


def message(mid: str = "m1", text: str = "hello") -> dict[str, Any]:
    return {"id": mid, "session": "codex-1", "recipient": "claude", "text": text, "repo": "Repository_Management"}


def gh_issue(state: str = "open", comments: int = 10) -> Any:
    return lambda argv: ScriptResult(0, json.dumps({"state": state, "comments": comments}), "")


def run(tmp_path: Path, board: ReadOnlyBoard, configured: int | None = 2376) -> dict[str, Any]:
    source = GitHubBoardSource(run=gh_issue(), configured=lambda: configured)
    return postman_runner.run_dry_cycle(
        board_port=board, source=source, roster_ids=("claude", "codex"), directory=tmp_path, clock=lambda: NOW
    )


def test_writes_are_refused() -> None:
    delivery = Delivery("claude", "message", "request", "hi", "m1", "pm-12345678")
    with pytest.raises(DryRunOnlyError):
        RefusingBus().send(delivery)
    with pytest.raises(DryRunOnlyError):
        RefusingBus().ack("claude", "m1")
    with pytest.raises(DryRunOnlyError):
        ReadOnlyBoard().register("claude", "pm-claude", 2376)


def test_dry_cycle_plans_a_delivery_and_sends_nothing(tmp_path: Path) -> None:
    board = ReadOnlyBoard(sessions_reader=sessions_ok, inbox_reader=inbox_with(message()))
    entry = run(tmp_path, board)
    assert entry["dry_run"] is True and entry["board_issue"] == 2376
    assert entry["counts"]["message"] == 1
    assert entry["counts"]["presence"] == 2  # pm-claude and pm-codex would be registered
    assert "error" not in entry
    assert not (tmp_path / postman_runner.STATE_FILE).exists()  # the cursor is never saved in a dry run


def test_reports_are_kept_newest_first_and_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(postman_runner, "REPORT_KEEP", 3)
    for n in range(5):
        board = ReadOnlyBoard(sessions_reader=sessions_ok, inbox_reader=inbox_with(message(f"m{n}")))
        run(tmp_path, board)
    reports = postman_runner.read_reports(tmp_path, limit=10)
    assert len(reports) == 3
    assert reports[0]["actions"][-1]["payload"]["board_message_id"] == "m4"


def test_unavailable_board_is_reported_not_raised(tmp_path: Path) -> None:
    board = ReadOnlyBoard(sessions_reader=lambda: {"available": False, "reason": "gh down"})
    entry = run(tmp_path, board)
    assert entry["error"] == "board unavailable: gh down"
    assert postman_runner.read_reports(tmp_path)[0]["error"] == entry["error"]


def test_issue_check_marks_full_board_and_names_configured_successor() -> None:
    source = GitHubBoardSource(run=gh_issue(comments=2500), configured=lambda: 2376)
    ref = source.inspect(1576)
    assert ref.full and not ref.closed and ref.successor == 2376
    assert GitHubBoardSource(run=gh_issue(), configured=lambda: 2376).inspect(2376).successor is None


def test_issue_check_failure_raises_board_unavailable() -> None:
    source = GitHubBoardSource(run=lambda argv: ScriptResult(1, "", "HTTP 403"), configured=lambda: None)
    with pytest.raises(BoardUnavailableError):
        source.inspect(2376)


def test_read_reports_without_file_is_empty(tmp_path: Path) -> None:
    assert postman_runner.read_reports(tmp_path) == []


def test_reports_route_is_read_only_and_never_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from coordination_fake_rm import install  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    for _ in install(tmp_path, monkeypatch, {}, repos="Tools"):
        monkeypatch.setenv(postman_runner.DATA_DIR_ENV, str(tmp_path / "postman"))
        run(tmp_path / "postman", ReadOnlyBoard(sessions_reader=sessions_ok, inbox_reader=inbox_with(message())))
        from server import app  # noqa: PLC0415

        client = TestClient(app, raise_server_exceptions=False, client=("100.64.0.9", 51000))
        body = client.get("/api/coordination/postman/reports?limit=5").json()
        assert body["live"] is False and len(body["reports"]) == 1
        assert body["reports"][0]["counts"]["message"] == 1
        assert client.get("/api/coordination/postman/reports?limit=0").status_code == 422


def test_seed_marks_the_backlog_so_the_next_dry_cycle_plans_none_of_it(tmp_path: Path) -> None:
    claim = message("c1", "Claimed RD #1 for codex")
    board = ReadOnlyBoard(sessions_reader=sessions_ok, inbox_reader=inbox_with(message(), claim))
    source = GitHubBoardSource(run=gh_issue(), configured=lambda: 2376)
    counts = postman_runner.seed_cursor(
        kinds=frozenset({"alert"}),
        board_port=board,
        source=source,
        roster_ids=("claude", "codex"),
        directory=tmp_path,
        clock=lambda: NOW,
    )
    assert counts == {"alert": 4}  # claude (addressee) on both, Barb and codex on the claim; nothing sent
    entry = run(tmp_path, board)
    assert "alert" not in entry["counts"] and entry["counts"]["message"] == 2  # messages were not seeded


def test_seed_refuses_unknown_kinds(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        postman_runner.seed_cursor(kinds=frozenset({"presence"}), directory=tmp_path)


def test_seed_with_a_cutoff_leaves_later_messages_for_delivery(tmp_path: Path) -> None:
    old = {**message("old"), "at": "2026-10-27T18:08:10+00:00"}
    new = {**message("new"), "at": "2026-10-27T22:30:00+00:00"}
    board = ReadOnlyBoard(sessions_reader=sessions_ok, inbox_reader=inbox_with(old, new))
    source = GitHubBoardSource(run=gh_issue(), configured=lambda: 2376)
    counts = postman_runner.seed_cursor(
        kinds=frozenset({"message"}),
        through=datetime(2026, 10, 27, 18, 8, 10, tzinfo=UTC),
        board_port=board,
        source=source,
        roster_ids=("claude", "codex"),
        directory=tmp_path,
        clock=lambda: NOW,
    )
    assert counts == {"message": 1}  # only the reviewed backlog; the later message still goes out
    entry = run(tmp_path, board)
    assert entry["counts"]["message"] == 1
