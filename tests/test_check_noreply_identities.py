"""Tests for scripts/check_noreply_identities.py."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts.check_noreply_identities import (
    check_range,
    load_allowlist,
    validate_noreply_email,
)

pytestmark = pytest.mark.unit

_ALLOWLIST_PATH = Path(__file__).resolve().parents[1] / "config" / "fleet_noreply_identities.json"
ALLOWLIST = load_allowlist(_ALLOWLIST_PATH)

GENERAL_EMAIL = "289268526+d-sorggeneralagent[bot]@users.noreply.github.com"
CLAUDE_EMAIL = "289268093+d-sorgclaudeagent[bot]@users.noreply.github.com"
UNNUMBERED_CURSOR = "d-sorgcursoragent[bot]@users.noreply.github.com"
UNNUMBERED_GEMINI = "d-sorggeminiagent[bot]@users.noreply.github.com"
UNKNOWN_EMAIL = "106227221+derHoizer@users.noreply.github.com"


def test_allowlisted_numbered_noreply_passes() -> None:
    assert validate_noreply_email(GENERAL_EMAIL, ALLOWLIST) is None
    assert validate_noreply_email(CLAUDE_EMAIL, ALLOWLIST) is None


def test_unnumbered_noreply_fails() -> None:
    for email in (UNNUMBERED_CURSOR, UNNUMBERED_GEMINI, "d-sorggeneralagent[bot]@users.noreply.github.com"):
        reason = validate_noreply_email(email, ALLOWLIST)
        assert reason is not None
        assert "must be" in reason


def test_unknown_numbered_noreply_fails() -> None:
    reason = validate_noreply_email(UNKNOWN_EMAIL, ALLOWLIST)
    assert reason is not None
    assert "106227221" in reason
    assert "allowlist" in reason


def test_mismatched_id_login_fails() -> None:
    wrong = "289268526+d-sorgclaudeagent[bot]@users.noreply.github.com"
    reason = validate_noreply_email(wrong, ALLOWLIST)
    assert reason is not None
    assert "allowlist" in reason


def test_non_noreply_is_ignored() -> None:
    assert validate_noreply_email("agent@example.com", ALLOWLIST) is None
    assert validate_noreply_email("cursoragent@cursor.com", ALLOWLIST) is None


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "tester")
    _git(path, "config", "user.email", "agent@example.com")
    _git(path, "config", "commit.gpgsign", "false")


def _commit(
    repo: Path,
    message: str,
    *,
    author_email: str,
    committer_email: str | None = None,
) -> None:
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "tester"
    env["GIT_AUTHOR_EMAIL"] = author_email
    env["GIT_COMMITTER_NAME"] = "tester"
    env["GIT_COMMITTER_EMAIL"] = committer_email or author_email
    _git(repo, "commit", "-m", message, env=env)


def _rev(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


def _seed(repo: Path, body: str = "base") -> str:
    (repo / "f").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "f")
    _commit(repo, body, author_email="agent@example.com")
    return _rev(repo)


def _allow_file(repo: Path) -> Path:
    path = repo / "allow.json"
    path.write_text(
        json.dumps({"identities": [{"id": "289268526", "login": "d-sorggeneralagent[bot]"}]}),
        encoding="utf-8",
    )
    return path


def test_co_authored_by_trailer_is_scanned(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    allow = load_allowlist(_allow_file(repo))
    base = _seed(repo)
    (repo / "f").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "f")
    _commit(
        repo,
        f"fix\n\nCo-authored-by: gemini <{UNNUMBERED_GEMINI}>\n",
        author_email="agent@example.com",
    )
    violations = check_range(repo, base, _rev(repo), allow)
    assert len(violations) == 1
    assert violations[0].field == "Co-authored-by"
    assert violations[0].email == UNNUMBERED_GEMINI


def test_unnumbered_author_email_fails_range(tmp_path: Path) -> None:
    repo = tmp_path / "author"
    _init_repo(repo)
    allow = load_allowlist(_allow_file(repo))
    base = _seed(repo)
    (repo / "f").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "f")
    _commit(repo, "bad author", author_email=UNNUMBERED_CURSOR)
    violations = check_range(repo, base, _rev(repo), allow)
    assert len(violations) == 2
    assert {item.field for item in violations} == {"author email", "committer email"}
    assert {item.email for item in violations} == {UNNUMBERED_CURSOR}


def test_unnumbered_committer_email_fails_range(tmp_path: Path) -> None:
    repo = tmp_path / "committer"
    _init_repo(repo)
    allow = load_allowlist(_allow_file(repo))
    base = _seed(repo)
    (repo / "f").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "f")
    _commit(
        repo,
        "bad committer",
        author_email="agent@example.com",
        committer_email=UNNUMBERED_CURSOR,
    )
    violations = check_range(repo, base, _rev(repo), allow)
    assert len(violations) == 1
    assert violations[0].field == "committer email"
    assert violations[0].email == UNNUMBERED_CURSOR


def test_unknown_trailer_and_history_before_base_are_scoped(tmp_path: Path) -> None:
    """Commits at or before base are merged history and stay out of the check."""
    repo = tmp_path / "scope"
    _init_repo(repo)
    (repo / "f").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "f")
    _commit(repo, "ancient bad", author_email=UNNUMBERED_CURSOR)
    base = _rev(repo)
    (repo / "f").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "f")
    _commit(
        repo,
        f"head\n\nCo-authored-by: stranger <{UNKNOWN_EMAIL}>\n",
        author_email=GENERAL_EMAIL,
    )
    violations = check_range(repo, base, _rev(repo), ALLOWLIST)
    assert len(violations) == 1
    assert violations[0].field == "Co-authored-by"
    assert violations[0].email == UNKNOWN_EMAIL


def test_allowlisted_commit_range_passes(tmp_path: Path) -> None:
    repo = tmp_path / "ok"
    _init_repo(repo)
    base = _seed(repo)
    (repo / "f").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "f")
    _commit(
        repo,
        f"feat\n\nCo-authored-by: claude <{CLAUDE_EMAIL}>\n",
        author_email=GENERAL_EMAIL,
    )
    assert check_range(repo, base, _rev(repo), ALLOWLIST) == []
