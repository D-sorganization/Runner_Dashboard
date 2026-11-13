"""Tests for F4 push-hygiene guard (issue #2892)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from backend.staff import git_hooks
from shared_scripts.push_hygiene import (
    COMMENT_MARKER,
    CommitInfo,
    PullRequestQueueState,
    evaluate_push_hygiene,
    evaluate_push_hygiene_with_recheck,
    format_hygiene_comment,
    inspect_git_commit,
    main,
    should_post_hygiene_comment,
)


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "d-sorggeneralagent[bot]",
        "GIT_COMMITTER_NAME": "d-sorggeneralagent[bot]",
        "GIT_AUTHOR_EMAIL": "289268526+d-sorggeneralagent[bot]@users.noreply.github.com",
        "GIT_COMMITTER_EMAIL": "289268526+d-sorggeneralagent[bot]@users.noreply.github.com",
    }
    res = subprocess.run(["git", *args], cwd=cwd, env=env, text=True, capture_output=True, check=True)
    return res.stdout.strip()


def _init_repo_with_origin(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    checkout = tmp_path / "checkout"
    _git(tmp_path, "init", "--bare", str(origin))
    _git(tmp_path, "init", "-b", "main", str(checkout))
    (checkout / "README.md").write_text("base\n", encoding="utf-8")
    _git(checkout, "add", "README.md")
    _git(checkout, "commit", "-m", "initial")
    _git(checkout, "remote", "add", "origin", str(origin))
    _git(checkout, "push", "-u", "origin", "main")
    return origin, checkout


_EMPTY = CommitInfo("aaa111111111", "tree00000000", ("bbb222222222",), ("tree00000000",), False, "chore: empty")
_MAIN_MERGE = CommitInfo(
    "ccc333333333",
    "tree22222222",
    ("bbb222222222", "ddd444444444"),
    ("tree11111111", "tree33333333"),
    True,
    "Merge main",
)
_NORMAL = CommitInfo("eee555555555", "tree99999999", ("bbb222222222",), ("tree11111111",), False, "fix: bug")
_REBASE = CommitInfo("fff666666666", "tree88888888", ("ddd444444444",), ("tree33333333",), False, "feat: rebased")
_SUB_MERGE = CommitInfo(
    "abc777777777",
    "tree77777777",
    ("bbb222222222", "eee888888888"),
    ("tree11111111", "tree66666666"),
    False,
    "Merge sub",
)


@pytest.mark.parametrize(
    ("case_name", "commit", "pr_state", "expected_allowed", "expected_violation"),
    [
        ("empty_when_auto_merge_on", _EMPTY, PullRequestQueueState(101, True, False), False, "empty_commit"),
        ("empty_when_in_merge_queue", _EMPTY, PullRequestQueueState(101, False, True), False, "empty_commit"),
        (
            "main_sync_when_auto_merge_on",
            _MAIN_MERGE,
            PullRequestQueueState(102, True, False),
            False,
            "main_sync_merge",
        ),
        (
            "main_sync_when_in_merge_queue",
            _MAIN_MERGE,
            PullRequestQueueState(102, True, True),
            False,
            "main_sync_merge",
        ),
        ("normal_commit_when_armed_and_queued", _NORMAL, PullRequestQueueState(103, True, True), True, ""),
        ("rebased_commit_when_armed", _REBASE, PullRequestQueueState(104, True, False), True, ""),
        ("non_main_merge_when_armed", _SUB_MERGE, PullRequestQueueState(105, True, False), True, ""),
        ("main_sync_when_unarmed_and_not_queued", _MAIN_MERGE, PullRequestQueueState(106, False, False), True, ""),
        ("closed_pr_ignores_stale_auto_merge", _EMPTY, PullRequestQueueState(107, True, False, "CLOSED"), True, ""),
    ],
)
def test_evaluate_push_hygiene_table(
    case_name: str,
    commit: CommitInfo,
    pr_state: PullRequestQueueState,
    expected_allowed: bool,
    expected_violation: str,
) -> None:
    decision = evaluate_push_hygiene(commit, pr_state)
    assert decision.allowed is expected_allowed, f"{case_name}: {decision}"
    assert decision.violation == expected_violation, f"{case_name}: {decision}"


@pytest.mark.parametrize(
    ("race_case", "initial_state", "refreshed_state", "expected_allowed", "expected_violation"),
    [
        (
            "pr_dequeued_mid_push_allows_main_sync",
            PullRequestQueueState(200, False, True),
            PullRequestQueueState(200, False, False),
            True,
            "",
        ),
        (
            "auto_merge_toggled_off_mid_push_allows_commit",
            PullRequestQueueState(201, True, False),
            PullRequestQueueState(201, False, False),
            True,
            "",
        ),
        (
            "auto_merge_toggled_on_mid_push_blocks_main_sync",
            PullRequestQueueState(202, False, False),
            PullRequestQueueState(202, True, False),
            False,
            "main_sync_merge",
        ),
    ],
)
def test_evaluate_push_hygiene_race_cases(
    race_case: str,
    initial_state: PullRequestQueueState,
    refreshed_state: PullRequestQueueState,
    expected_allowed: bool,
    expected_violation: str,
) -> None:
    decision = evaluate_push_hygiene_with_recheck(_MAIN_MERGE, initial_state, refresh_state=lambda: refreshed_state)
    assert decision.allowed is expected_allowed, f"{race_case}: {decision}"
    assert decision.violation == expected_violation, f"{race_case}: {decision}"


def test_dbc_preconditions_reject_invalid_commit_info() -> None:
    state = PullRequestQueueState(pr_number=1, auto_merge_enabled=True)
    with pytest.raises(ValueError, match="sha"):
        evaluate_push_hygiene(CommitInfo("", "t", ("p",), ("pt",)), state)
    with pytest.raises(ValueError, match="parent_tree_shas"):
        evaluate_push_hygiene(CommitInfo("s", "t", ("p1", "p2"), ("pt1",)), state)


def test_inspect_git_commit_detects_normal_empty_merge_and_rebase(tmp_path: Path) -> None:
    _, checkout = _init_repo_with_origin(tmp_path)
    _git(checkout, "checkout", "-b", "feat/2892-test")
    (checkout / "feature.py").write_text("x = 1\n", encoding="utf-8")
    _git(checkout, "add", "feature.py")
    _git(checkout, "commit", "-m", "feat: add feature")

    armed = PullRequestQueueState(pr_number=2892, auto_merge_enabled=True)
    assert evaluate_push_hygiene(inspect_git_commit(checkout, "HEAD"), armed).allowed is True

    _git(checkout, "commit", "--allow-empty", "-m", "chore: empty retrigger")
    empty_decision = evaluate_push_hygiene(inspect_git_commit(checkout, "HEAD"), armed)
    assert not empty_decision.allowed and empty_decision.violation == "empty_commit"

    _git(checkout, "reset", "--hard", "HEAD~1")
    _git(checkout, "checkout", "main")
    (checkout / "main_only.txt").write_text("main advance\n", encoding="utf-8")
    _git(checkout, "add", "main_only.txt")
    _git(checkout, "commit", "-m", "chore: advance main")
    _git(checkout, "push", "origin", "main")

    _git(checkout, "checkout", "feat/2892-test")
    _git(checkout, "merge", "--no-ff", "origin/main", "-m", "Merge origin/main")
    merge_info = inspect_git_commit(checkout, "HEAD")
    assert merge_info.second_parent_is_main is True
    assert evaluate_push_hygiene(merge_info, armed).violation == "main_sync_merge"

    _git(checkout, "reset", "--hard", "HEAD~1")
    _git(checkout, "rebase", "origin/main")
    assert evaluate_push_hygiene(inspect_git_commit(checkout, "HEAD"), armed).allowed is True


def test_idempotent_comment_and_cli_main(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    decision = evaluate_push_hygiene(_EMPTY, PullRequestQueueState(pr_number=55, auto_merge_enabled=True))
    body = format_hygiene_comment(decision, head_sha=_EMPTY.sha)
    assert COMMENT_MARKER in body
    assert should_post_hygiene_comment([], head_sha=_EMPTY.sha) is True
    assert should_post_hygiene_comment([body], head_sha="other_sha") is False

    _, checkout = _init_repo_with_origin(tmp_path)
    _git(checkout, "checkout", "-b", "feat/2892-cli")
    _git(checkout, "commit", "--allow-empty", "-m", "empty")
    rc = main(["--repo-root", str(checkout), "--rev", "HEAD", "--auto-merge-enabled"])
    captured = capsys.readouterr()
    assert rc == 1 and "::error::" in captured.err + captured.out


def test_staff_git_hooks_pre_push_blocks_empty_commit_when_branch_armed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, checkout = _init_repo_with_origin(tmp_path)
    _git(checkout, "checkout", "-b", "codex/2892-hook")
    (checkout / "changes").mkdir()
    (checkout / "changes/2892-hook.md").write_text(" fragment\n", encoding="utf-8")
    _git(checkout, "add", "changes/2892-hook.md")
    _git(checkout, "commit", "-m", "feat: add change fragment")

    head_sha = _git(checkout, "rev-parse", "HEAD")
    ref_line = f"refs/heads/codex/2892-hook {head_sha} refs/heads/codex/2892-hook {'0' * 40}\n"
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("STAFF_PR_AUTO_MERGE", "1")
    assert git_hooks.run_pre_push_checks(checkout, ref_line) == 0

    _git(checkout, "commit", "--allow-empty", "-m", "chore: empty")
    empty_head = _git(checkout, "rev-parse", "HEAD")
    empty_ref_line = f"refs/heads/codex/2892-hook {empty_head} refs/heads/codex/2892-hook {head_sha}\n"
    assert git_hooks.run_pre_push_checks(checkout, empty_ref_line) == 1
