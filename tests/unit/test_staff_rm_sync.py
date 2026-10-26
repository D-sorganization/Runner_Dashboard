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


def test_consecutive_skips_raise_attention_and_success_resets_counter(repo, monkeypatch):
    import staff.rm_sync as rm_sync

    _, clone, state = repo
    (clone / "owner.txt").write_text("owner work\n")
    now = [1_800_000_000.0]
    monkeypatch.setattr(rm_sync.time, "time", lambda: now[0])

    for expected_count in range(1, rm_sync.SKIPPED_ATTENTION_THRESHOLD + 1):
        result = refresh(clone, state_path=state)
        assert result["consecutive_skips"] == expected_count
        assert result["attention"] is (expected_count >= rm_sync.SKIPPED_ATTENTION_THRESHOLD)
        now[0] += rm_sync.INTERVAL_SECONDS

    (clone / "owner.txt").unlink()
    result = refresh(clone, state_path=state)
    assert result["status"] == "unchanged"
    assert result["consecutive_skips"] == 0
    assert result["attention"] is False


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


def test_status_backups_are_pruned_to_newest_keep(tmp_path):
    from staff.rm_sync import BACKUP_KEEP, _write

    state = tmp_path / "rm_source_status.json"
    # Simulate the unbounded history a long-running node already accumulated.
    stale = [state.with_name(f"{state.name}.bak-2026-10-01-0000{i:02d}-000000") for i in range(BACKUP_KEEP + 5)]
    for path in stale:
        path.write_text("{}", encoding="utf-8")
    unrelated = tmp_path / "other.json.bak-2026-10-01-000000-000000"
    unrelated.write_text("{}", encoding="utf-8")

    for n in range(3):
        _write(state, {"checked_at": n})

    backups = sorted(tmp_path.glob(f"{state.name}.bak-*"))
    assert len(backups) == BACKUP_KEEP
    assert stale[-1] in backups and stale[0] not in backups
    assert json.loads(state.read_text(encoding="utf-8")) == {"checked_at": 2}
    assert unrelated.exists()
