from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.recurring_scan import (
    Cursor,
    Finding,
    build_finding_alert,
    build_heartbeat_alert,
    filter_since,
    heartbeat_status,
    load_cursor,
    run_scan,
    save_cursor,
)

FAKE_TOKEN = "ghp_" + "A" * 36  # shape only; never a real credential
REPO_NAME = "D-sorganization/Runner_Dashboard"
_EMAIL = "t@x.com"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": _EMAIL, "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": _EMAIL}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env={**os.environ, **GIT_ENV}
    )


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _init_repo(path: Path) -> Path:
    repo = path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "baseline")
    return repo


def _commit(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"add {name}")


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def __call__(self, msg: dict[str, Any]) -> dict[str, Any]:
        self.sent.append(msg)
        return {"ok": True, "status": "sent"}


def _scan(repo: Path, cursor: Cursor, notifier: FakeNotifier, **over: Any) -> list[dict[str, Any]]:
    kwargs: dict[str, Any] = {"fetch_issues": lambda *_: [], "fetch_comments": lambda *_: [], "notify": notifier}
    kwargs.update(over)
    _, alerts = run_scan(repo, REPO_NAME, cursor, **kwargs)
    return alerts


@pytest.mark.parametrize(
    ("last_run_at", "now", "max_gap", "want"),
    [
        (None, 1000.0, 60.0, "ok"),
        (1000.0, 1030.0, 60.0, "ok"),
        (1000.0, 1061.0, 60.0, "missed"),
    ],
)
def test_heartbeat_status(last_run_at: float | None, now: float, max_gap: float, want: str) -> None:
    assert heartbeat_status(last_run_at, now=now, max_gap_seconds=max_gap) == want


def test_heartbeat_status_rejects_non_positive_gap() -> None:
    with pytest.raises(ValueError, match="positive"):
        heartbeat_status(None, now=0.0, max_gap_seconds=0.0)


def test_filter_since_keeps_only_strictly_newer_items() -> None:
    items = [{"created_at": "2026-10-20T00:00:00Z"}, {"created_at": "2026-10-28T00:00:00Z"}]
    assert filter_since(items, "2026-10-25T00:00:00Z") == [items[1]]
    assert filter_since(items, None) == items


def test_cursor_round_trips_through_disk_and_missing_file_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "cursor.json"
    cursor = Cursor(last_commit="abc123", last_item_at="2026-10-28T00:00:00Z", last_run_at=123.0)
    save_cursor(path, cursor)
    assert load_cursor(path) == cursor
    assert load_cursor(tmp_path / "missing.json") == Cursor()


def test_finding_alert_never_contains_the_matched_text() -> None:
    alert = build_finding_alert([Finding(source="commit:abc123", category="github-token")])
    assert "github-token" in alert["summary"]
    assert FAKE_TOKEN not in alert["body"]
    assert alert["kind"] == "alert"
    assert "commit:abc123" in alert["body"]


def test_heartbeat_alert_names_the_gap_and_is_a_private_alert() -> None:
    alert = build_heartbeat_alert(max_gap_seconds=36 * 3600)
    assert alert["kind"] == "alert"
    assert "36h" in alert["body"]
    assert alert["dedupe_key"] == "scan-heartbeat-missed"


def test_first_run_establishes_a_baseline_without_retroactive_scan(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "secret.txt", FAKE_TOKEN)  # pre-existing history; already inventoried by #3081
    notifier = FakeNotifier()

    def _fail_if_called(*_args: Any) -> list[dict[str, Any]]:
        raise AssertionError("first run must not fetch issues/comments retroactively")

    new_cursor, alerts = run_scan(
        repo,
        REPO_NAME,
        Cursor(),
        now=1000.0,
        fetch_issues=_fail_if_called,
        fetch_comments=_fail_if_called,
        notify=notifier,
    )
    assert alerts == []
    assert notifier.sent == []
    assert new_cursor.last_commit == _head(repo)
    assert new_cursor.last_item_at is not None
    assert new_cursor.last_run_at == 1000.0


def test_seeded_commit_finding_raises_one_private_alert_and_no_public_comment(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    baseline = _head(repo)
    _commit(repo, "clean.txt", "nothing sensitive here\n")
    _commit(repo, "secret.txt", FAKE_TOKEN + "\n")
    notifier = FakeNotifier()
    alerts = _scan(repo, Cursor(last_commit=baseline, last_run_at=1000.0), notifier, now=1010.0)
    assert len(alerts) == 1
    (sent,) = notifier.sent
    assert sent["kind"] == "alert"
    assert "github-token" in sent["summary"]
    assert FAKE_TOKEN not in sent["body"]


def test_only_new_issues_and_comments_since_cursor_are_scanned(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    old_item = {"number": 1, "created_at": "2026-10-01T00:00:00Z", "body": "fine, " + FAKE_TOKEN}
    new_clean = {"number": 2, "created_at": "2026-10-28T00:00:00Z", "body": "nothing sensitive"}
    new_hit = {"number": 3, "created_at": "2026-10-28T01:00:00Z", "body": "leaked: " + FAKE_TOKEN}
    notifier = FakeNotifier()
    cursor = Cursor(last_commit=_head(repo), last_item_at="2026-10-15T00:00:00Z", last_run_at=1000.0)
    alerts = _scan(repo, cursor, notifier, now=1010.0, fetch_issues=lambda *_: [old_item, new_clean, new_hit])
    assert len(alerts) == 1
    (sent,) = notifier.sent
    assert "issue:3" in sent["body"]
    assert "issue:1" not in sent["body"]
    assert "issue:2" not in sent["body"]


@pytest.mark.parametrize(("gap", "want_alerts"), [(36 * 3600 + 1, 1), (60, 0)])
def test_heartbeat_alert_fires_only_once_the_gap_is_exceeded(tmp_path: Path, gap: float, want_alerts: int) -> None:
    repo = _init_repo(tmp_path)
    notifier = FakeNotifier()
    cursor = Cursor(last_commit=_head(repo), last_item_at="2026-10-15T00:00:00Z", last_run_at=1000.0)
    alerts = _scan(repo, cursor, notifier, now=1000.0 + gap, max_gap_seconds=36 * 3600)
    assert len(alerts) == want_alerts
    if want_alerts:
        assert notifier.sent[0]["dedupe_key"] == "scan-heartbeat-missed"


def _workflow_parent_line() -> str:
    """The cursor-save step's PARENT= line from recurring-scan.yml, run verbatim below (#3638)."""
    workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "recurring-scan.yml"
    script = next(
        step["run"]
        for step in yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]["scan"]["steps"]
        if "STATE_BRANCH" in step.get("run", "")
    )
    return next(line.strip() for line in script.splitlines() if line.strip().startswith("PARENT="))


def _workflow_parent(repo: Path) -> str:
    snippet = f'STATE_BRANCH=bot/recurring-scan-state\n{_workflow_parent_line()}\nprintf %s "$PARENT"'
    return subprocess.run(
        ["bash", "-c", snippet], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_first_run_without_state_branch_commits_with_no_parent(tmp_path: Path) -> None:
    # A missing ref must yield an empty PARENT, not its own name echoed back (#3638).
    assert _workflow_parent(_init_repo(tmp_path)) == ""


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_existing_state_branch_becomes_the_parent(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _git(repo, "update-ref", "refs/remotes/origin/bot/recurring-scan-state", "HEAD")
    assert _workflow_parent(repo) == _head(repo)
