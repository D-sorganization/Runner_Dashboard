"""code-reviewer must not occupy dispatch overlap paths (#4430 read-only exemption)."""

from __future__ import annotations

from pathlib import Path

import pytest
from dispatch_queue_link import reset_dispatch_queue_link_state
from dispatch_routing import dispatch_queue_manager
from dispatch_session_policy import role_occupies_paths
from staff import roles as roles_mod


@pytest.fixture(autouse=True)
def _clean_queue() -> None:
    reset_dispatch_queue_link_state()
    yield
    reset_dispatch_queue_link_state()


@pytest.fixture(autouse=True)
def _reset_role_cache() -> None:
    roles_mod.clear_roles_cache()
    yield
    roles_mod.clear_roles_cache()


def test_bundled_code_reviewer_role_does_not_occupy_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RM YAML without flags is overridden by Runner_Dashboard staff/roles/code-reviewer.yml."""
    rm_roles = tmp_path / "rm-roles"
    rm_roles.mkdir()
    (rm_roles / "code-reviewer.yml").write_text(
        "name: code-reviewer\ntitle: Code Reviewer\nproviders: [codex]\n"
        "permissions:\n  lease: false\n  push_branch: false\n  open_pr: false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STAFF_ROLES_DIR", str(rm_roles))
    spec = roles_mod.load_roles()["code-reviewer"]
    assert spec.code_read_only is True
    assert spec.occupies_paths is False
    assert role_occupies_paths(spec) is False


def test_two_concurrent_code_reviewer_reviews_same_repo_both_admitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rm_roles = tmp_path / "rm-roles"
    rm_roles.mkdir()
    (rm_roles / "code-reviewer.yml").write_text(
        "name: code-reviewer\ntitle: Code Reviewer\nproviders: [codex]\n"
        "permissions:\n  lease: false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STAFF_ROLES_DIR", str(rm_roles))
    spec = roles_mod.load_roles()["code-reviewer"]
    occupies = role_occupies_paths(spec)
    assert occupies is False

    first = dispatch_queue_manager.try_dispatch(
        repository="Runner_Dashboard",
        prompt="Review `backend/shared.py` for PR #10",
        occupies_paths=occupies,
        role="code-reviewer",
        number=10,
    )
    second = dispatch_queue_manager.try_dispatch(
        repository="Runner_Dashboard",
        prompt="Review `backend/shared.py` for PR #11",
        occupies_paths=occupies,
        role="code-reviewer",
        number=11,
    )
    assert first.dispatched is True
    assert second.dispatched is True
    assert first.session is None
    assert second.session is None
    assert dispatch_queue_manager.get_active_sessions() == []