"""Concurrent same-repo dispatch sessions when claims do not overlap (#4492).

A dispatch whose prompt declares no file paths used to claim ``*`` (the whole repo),
so any second write session in that repo was queued with ``queued_overlapping_paths``.
A dispatch that names its target PR or issue now claims only that target, so different
PRs / issues in one repo run side by side up to a per-repo cap (default 3). Real path
overlap, the same PR / issue, and target-less wildcard sessions still block.
"""

from __future__ import annotations

import pytest
from dispatch_routing import DispatchQueueManager, extract_declared_paths, paths_overlap
from dispatch_session_policy import (
    DEFAULT_MAX_SESSIONS_PER_REPO,
    max_sessions_per_repo,
)


@pytest.fixture(autouse=True)
def _clear_cap_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISPATCH_MAX_SESSIONS_PER_REPO", raising=False)
    monkeypatch.delenv("DISPATCH_MAX_SESSIONS_PER_REPO_OVERRIDES", raising=False)


def _mgr() -> DispatchQueueManager:
    return DispatchQueueManager()


def test_default_cap_is_three() -> None:
    assert DEFAULT_MAX_SESSIONS_PER_REPO == 3
    assert max_sessions_per_repo("AffineDrift") == 3


def test_cap_env_and_per_repo_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISPATCH_MAX_SESSIONS_PER_REPO", "4")
    monkeypatch.setenv("DISPATCH_MAX_SESSIONS_PER_REPO_OVERRIDES", "UpstreamDrift=2, D-sorganization/AffineDrift=5,bad")
    assert max_sessions_per_repo("Runner_Dashboard") == 4
    assert max_sessions_per_repo("upstreamdrift") == 2
    assert max_sessions_per_repo("AffineDrift") == 5
    assert max_sessions_per_repo("D-sorganization/UpstreamDrift") == 2


@pytest.mark.parametrize("raw", ["0", "-1", "abc", ""])
def test_invalid_cap_falls_back_to_default(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("DISPATCH_MAX_SESSIONS_PER_REPO", raw)
    assert max_sessions_per_repo("AffineDrift") == 3


def test_two_pathless_dispatches_on_different_prs_both_run() -> None:
    mgr = _mgr()
    a = mgr.try_dispatch(repository="AffineDrift", pr_number=5759, prompt="fix the failing checks")
    b = mgr.try_dispatch(repository="AffineDrift", pr_number=5760, prompt="fix the failing checks")
    assert a.dispatched and b.dispatched
    assert len(mgr.get_active_sessions("AffineDrift")) == 2


def test_pathless_dispatches_on_different_issues_both_run() -> None:
    mgr = _mgr()
    assert mgr.try_dispatch(repository="UpstreamDrift", number=12692, prompt="implement it").dispatched
    assert mgr.try_dispatch(repository="UpstreamDrift", number=12693, prompt="implement it").dispatched


def test_same_pr_is_still_rejected() -> None:
    mgr = _mgr()
    first = mgr.try_dispatch(repository="AffineDrift", pr_number=5759, prompt="fix")
    second = mgr.try_dispatch(repository="AffineDrift", pr_number=5759, prompt="fix")
    assert first.dispatched and not second.dispatched
    assert second.queued_item is not None
    assert second.queued_item.blocking_session_id == first.session.session_id  # type: ignore[union-attr]
    assert second.queued_item.block_reason == "same_target"


def test_same_issue_is_rejected_even_with_disjoint_paths() -> None:
    mgr = _mgr()
    assert mgr.try_dispatch(repository="RD", number=7, declared_paths=["backend/a.py"]).dispatched
    second = mgr.try_dispatch(repository="RD", number=7, declared_paths=["frontend/b.tsx"])
    assert not second.dispatched
    assert second.queued_item is not None and second.queued_item.block_reason == "same_target"


def test_real_path_overlap_is_still_rejected() -> None:
    mgr = _mgr()
    assert mgr.try_dispatch(repository="RD", number=1, declared_paths=["backend/server.py"]).dispatched
    second = mgr.try_dispatch(repository="RD", number=2, declared_paths=["backend"])
    assert not second.dispatched
    assert second.queued_item is not None and second.queued_item.block_reason == "overlapping_paths"


def test_disjoint_declared_paths_run_together() -> None:
    mgr = _mgr()
    assert mgr.try_dispatch(repository="RD", number=1, declared_paths=["backend/a.py"]).dispatched
    assert mgr.try_dispatch(repository="RD", number=2, declared_paths=["frontend/b.tsx"]).dispatched


def test_targetless_pathless_dispatch_still_claims_whole_repo() -> None:
    mgr = _mgr()
    assert mgr.try_dispatch(repository="RD", prompt="tidy things up").dispatched
    assert not mgr.try_dispatch(repository="RD", pr_number=9, prompt="fix").dispatched


def test_per_repo_cap_queues_the_next_session(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _mgr()
    for pr in (1, 2, 3):
        assert mgr.try_dispatch(repository="AffineDrift", pr_number=pr, prompt="fix").dispatched
    fourth = mgr.try_dispatch(repository="AffineDrift", pr_number=4, prompt="fix")
    assert not fourth.dispatched
    assert fourth.queued_item is not None and fourth.queued_item.block_reason == "repo_session_cap"
    # The cap is per repo: another repo is unaffected.
    assert mgr.try_dispatch(repository="Tools", pr_number=4, prompt="fix").dispatched


def test_cap_override_allows_more(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISPATCH_MAX_SESSIONS_PER_REPO_OVERRIDES", "AffineDrift=4")
    mgr = _mgr()
    for pr in (1, 2, 3, 4):
        assert mgr.try_dispatch(repository="AffineDrift", pr_number=pr, prompt="fix").dispatched
    assert not mgr.try_dispatch(repository="AffineDrift", pr_number=5, prompt="fix").dispatched


def test_releasing_a_session_frees_a_cap_slot() -> None:
    mgr = _mgr()
    sessions = [mgr.try_dispatch(repository="AD", pr_number=n, prompt="fix").session for n in (1, 2, 3)]
    assert not mgr.try_dispatch(repository="AD", pr_number=4, prompt="fix").dispatched
    mgr.release_session(sessions[0].session_id)  # type: ignore[union-attr]
    assert mgr.try_dispatch(repository="AD", pr_number=4, prompt="fix").dispatched


def test_read_only_roles_do_not_count_toward_the_cap() -> None:
    mgr = _mgr()
    for pr in (1, 2, 3):
        assert mgr.try_dispatch(repository="AD", pr_number=pr, prompt="fix").dispatched
    assert mgr.try_dispatch(repository="AD", pr_number=4, prompt="review", occupies_paths=False).dispatched


def test_extract_declared_paths_is_unchanged_for_pathless_prompts() -> None:
    assert extract_declared_paths("fix the failing checks") == ["*"]


def test_target_claims_only_overlap_the_same_target() -> None:
    assert paths_overlap(["#target/5"], ["#target/5"]) is True
    assert paths_overlap(["#target/5"], ["#target/6"]) is False
    assert paths_overlap(["#target/5"], ["*"]) is True
