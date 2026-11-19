"""Tests for scripts/check_pr_closing_board.py (issue #4574)."""

from __future__ import annotations

import json

import pytest

from scripts import automerge_guard, check_pr_closing_board


def test_validate_clean_body_returns_zero(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(automerge_guard, "_configured_board_issues", lambda: {3029, 2539, 2851})
    assert check_pr_closing_board.main(["--body", "Fixes bug\n\nCloses #4574"]) == 0
    assert "PASS" in capsys.readouterr().out


def test_validate_body_closing_active_board_returns_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(automerge_guard, "_configured_board_issues", lambda: {3029, 2539, 2851})
    assert check_pr_closing_board.main(["--body", "Closes #3029"]) == 1
    err = capsys.readouterr().err
    assert "REJECT" in err and "RM #3029" in err


def test_validate_body_closing_predecessor_board_returns_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(automerge_guard, "_configured_board_issues", lambda: {3029, 2539, 2851})
    assert check_pr_closing_board.main(["--body", "Fixes Repository_Management#2851"]) == 1
    err = capsys.readouterr().err
    assert "REJECT" in err and "RM #2851" in err


def test_reads_from_env_pr_body_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(automerge_guard, "_configured_board_issues", lambda: {3029, 2539, 2851})
    monkeypatch.setenv("PR_BODY_JSON", json.dumps("Resolves #3029"))
    assert check_pr_closing_board.main([]) == 1
    assert "RM #3029" in capsys.readouterr().err
