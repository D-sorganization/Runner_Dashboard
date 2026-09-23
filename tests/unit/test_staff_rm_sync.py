"""Real local Git histories exercise safe RM updates without network access."""

import json
import subprocess
from pathlib import Path

import pytest
from staff.rm_sync import refresh, source_status
from staff.roles import load_roles


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    config = tmp_path / "staff.gitconfig"
    config.write_text("[user]\n name = Test\n email = test@example.invalid\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    roles = origin / "staff/roles"
    roles.mkdir(parents=True)
    (roles / "worker.yml").write_text("name: worker\nproviders: [claude]\n", encoding="utf-8")
    git(origin, "add", ".")
    git(origin, "commit", "-m", "initial")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", str(origin), str(clone))
    monkeypatch.setenv("STAFF_RM_ROOT", str(clone))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(clone / "staff/roles"))
    return origin, clone, tmp_path / "state.json"


def advance(origin):
    (origin / "staff/roles/worker.yml").write_text("name: worker\nproviders: [claude-ollama]\n", encoding="utf-8")
    git(origin, "add", ".")
    git(origin, "commit", "-m", "update provider")
    return git(origin, "rev-parse", "HEAD")


def test_fast_forward_reloads_roles_and_exposes_revision(repo):
    origin, clone, state = repo
    before = git(clone, "rev-parse", "HEAD")
    assert load_roles()["worker"].providers == ("claude",)
    target = advance(origin)
    result = refresh(clone, state_path=state)
    assert result["status"] == "updated"
    assert git(clone, "rev-parse", "HEAD") == target
    assert git(clone, "rev-parse", result["backup_ref"]) == before
    assert load_roles()["worker"].providers == ("claude-ollama",)
    status = source_status(clone, state_path=state)
    assert status["commit"] == target
    assert status["commit_age_seconds"] >= 0
    assert status["check_age_seconds"] >= 0


@pytest.mark.parametrize("kind", ["dirty", "untracked", "diverged", "branch"])
def test_unsafe_checkout_is_preserved(repo, kind):
    origin, clone, state = repo
    advance(origin)
    if kind == "dirty":
        (clone / "staff/roles/worker.yml").write_text("owner edit\n")
    elif kind == "untracked":
        (clone / "owner.txt").write_text("do not delete\n")
    elif kind == "branch":
        git(clone, "checkout", "-b", "owner-work")
    else:
        (clone / "owner.txt").write_text("local commit\n")
        git(clone, "add", ".")
        git(clone, "commit", "-m", "owner work")
    before = git(clone, "rev-parse", "HEAD")
    result = refresh(clone, state_path=state)
    assert result["status"] == "skipped"
    assert git(clone, "rev-parse", "HEAD") == before


def test_throttle_persists_between_invocations(repo):
    origin, clone, state = repo
    refresh(clone, state_path=state)
    before = git(clone, "rev-parse", "HEAD")
    advance(origin)
    assert refresh(clone, state_path=state)["status"] == "unchanged"
    assert git(clone, "rev-parse", "HEAD") == before


def test_fetch_failure_is_visible_without_failing_dashboard(repo):
    _, clone, state = repo
    git(clone, "remote", "set-url", "origin", str(clone / "missing"))
    result = refresh(clone, state_path=state)
    assert result["status"] == "error"
    assert source_status(clone, state_path=state)["commit"] == git(clone, "rev-parse", "HEAD")


def test_status_does_not_report_state_from_another_clone(repo):
    _, clone, state = repo
    state.write_text(json.dumps({"root": "another-node", "commit": "wrong"}))
    assert source_status(clone, state_path=state)["commit"] is None
