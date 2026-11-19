"""Tests for atomic, crash-recoverable board rollover (issue #4574)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from coordination import board_rollover as br_mod


def _make_rm_config(tmp_path: Path, issue: int = 3029, preds: list[int] | None = None) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = config_dir / "agent_communication.json"
    data = {
        "version": 1,
        "board_repo": "D-sorganization/Repository_Management",
        "board_issue": issue,
        "board_predecessors": [2376, 2539, 2851] if preds is None else preds,
    }
    cfg_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return tmp_path


@pytest.mark.unit
def test_rollover_skipped_when_below_threshold(tmp_path: Path) -> None:
    rm_root = _make_rm_config(tmp_path, issue=3029)
    runner = MagicMock()
    # Returns comment count 420
    runner.return_value = MagicMock(returncode=0, stdout=json.dumps({"comments": 420}), stderr="")

    res = br_mod.execute_board_rollover(
        rm_root,
        threshold=500,
        runner=runner,
        journal_path=tmp_path / "rollover_journal.json",
    )
    assert res.rolled_over is False
    assert res.old_issue == 3029
    assert res.new_issue is None
    assert "below threshold" in res.reason


@pytest.mark.unit
def test_rollover_executes_at_or_above_500_comments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rm_root = _make_rm_config(tmp_path, issue=3029, preds=[2539, 2851])
    calls = []

    def fake_runner(cmd: list[str]) -> MagicMock:
        calls.append(cmd)
        cmd_str = " ".join(cmd)
        if "repos/D-sorganization/Repository_Management/issues/3029" in cmd_str:
            return MagicMock(returncode=0, stdout=json.dumps({"comments": 505}), stderr="")
        if "issue create" in cmd_str or "issues" in cmd_str and "POST" in cmd_str:
            # gh issue create returns issue URL or json with number
            issue_url = "https://github.com/D-sorganization/Repository_Management/issues/3035\n"
            return MagicMock(returncode=0, stdout=issue_url, stderr="")
        return MagicMock(returncode=0, stdout="{}", stderr="")

    cache_reset = MagicMock()
    monkeypatch.setattr("coordination.board.reset_cache", cache_reset)

    journal = tmp_path / "rollover_journal.json"
    res = br_mod.execute_board_rollover(
        rm_root,
        threshold=500,
        runner=fake_runner,
        journal_path=journal,
    )

    assert res.rolled_over is True
    assert res.old_issue == 3029
    assert res.new_issue == 3035
    assert res.predecessors == [2539, 2851, 3029]

    # Verify agent_communication.json updated
    cfg_data = json.loads((rm_root / "config" / "agent_communication.json").read_text(encoding="utf-8"))
    assert cfg_data["board_issue"] == 3035
    assert cfg_data["board_predecessors"] == [2539, 2851, 3029]

    # Verify cache reset called
    cache_reset.assert_called_once()


@pytest.mark.unit
def test_rollover_crash_recovery_resumes_without_duplicate_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rm_root = _make_rm_config(tmp_path, issue=3029, preds=[2539, 2851])
    journal = tmp_path / "rollover_journal.json"

    # Simulate crash right after issue creation
    journal.write_text(
        json.dumps(
            {
                "stage": "ISSUE_CREATED",
                "old_issue": 3029,
                "new_issue": 3040,
                "predecessors": [2539, 2851],
            }
        ),
        encoding="utf-8",
    )

    calls = []

    def fake_runner(cmd: list[str]) -> MagicMock:
        calls.append(cmd)
        return MagicMock(returncode=0, stdout="{}", stderr="")

    res = br_mod.execute_board_rollover(
        rm_root,
        threshold=500,
        runner=fake_runner,
        journal_path=journal,
    )

    assert res.rolled_over is True
    assert res.old_issue == 3029
    assert res.new_issue == 3040

    # Ensure issue create was NOT called again (recovered from journal)
    for cmd in calls:
        assert "issue create" not in " ".join(cmd)

    # Config must now be updated with new issue 3040
    cfg_data = json.loads((rm_root / "config" / "agent_communication.json").read_text(encoding="utf-8"))
    assert cfg_data["board_issue"] == 3040
    assert cfg_data["board_predecessors"] == [2539, 2851, 3029]


@pytest.mark.unit
def test_rollover_preconditions_and_dbc_validation(tmp_path: Path) -> None:
    rm_root = _make_rm_config(tmp_path, issue=3029)
    with pytest.raises(ValueError, match="threshold must be a positive integer"):
        br_mod.execute_board_rollover(rm_root, threshold=0)

    # Missing config raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        br_mod.execute_board_rollover(tmp_path / "nonexistent", threshold=500)


@pytest.mark.unit
def test_forced_rollover_bypasses_threshold_check(tmp_path: Path) -> None:
    rm_root = _make_rm_config(tmp_path, issue=3029, preds=[2376, 2539, 2851])

    def fake_runner(cmd: list[str]) -> MagicMock:
        cmd_str = " ".join(cmd)
        if "issue create" in cmd_str:
            return MagicMock(returncode=0, stdout="3050\n", stderr="")
        return MagicMock(returncode=0, stdout="{}", stderr="")

    journal = tmp_path / "rollover_journal.json"
    res = br_mod.execute_board_rollover(
        rm_root,
        threshold=500,
        force=True,
        runner=fake_runner,
        journal_path=journal,
    )
    assert res.rolled_over is True
    assert res.old_issue == 3029
    assert res.new_issue == 3050
    # Must cap predecessors at 3
    assert len(res.predecessors) == 3
    assert res.predecessors == [2539, 2851, 3029]
