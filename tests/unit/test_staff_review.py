"""Unit tests for code-reviewer runtime (WP-1.3, Issue #1518)."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from cache_utils import cache_clear, cache_set
from projects.priorities import ProjectPriority
from staff.action_executors import DEFAULT_REVIEWER_ROLE
from staff.review import (
    ALTERNATE_MODELS,
    DEFAULT_REVIEW_FOCUS,
    FALLBACK_P0_P1_REPOS,
    PROVIDER_FAMILY,
    auto_review_if_eligible,
    detect_author_provider,
    parse_outcome,
    parse_review_verdict,
    prepare_review_params,
    review_scope_repos,
    select_reviewer_provider,
)
from staff.store import RunRecord


def make_run(**kwargs: Any) -> RunRecord:
    defaults: dict[str, Any] = {
        "id": "run-test",
        "role": "pragmatic-programmer",
        "provider": "codex",
        "model": "default",
        "machine": "local",
        "repo": "Runner_Dashboard",
        "target_kind": "pr",
        "target_ref": "42",
        "prompt": "Test prompt",
    }
    defaults.update(kwargs)
    return RunRecord(**defaults)


class TestProviderSelection:
    def test_author_claude_picks_different_provider(self) -> None:
        """When author is claude, pick first available provider from a different family."""
        role_providers = ["claude", "codex", "gemini", "antigravity"]
        sel = select_reviewer_provider(author_provider="claude", role_providers=role_providers)
        assert sel.provider != "claude"
        assert PROVIDER_FAMILY.get(sel.provider) != PROVIDER_FAMILY.get("claude")
        assert sel.provider == "codex"
        assert not sel.same_provider

    def test_author_unknown_picks_first_listed_provider(self) -> None:
        """When author is unknown, use the first listed provider in role.providers."""
        role_providers = ["claude", "codex", "gemini"]
        sel = select_reviewer_provider(author_provider=None, role_providers=role_providers)
        assert sel.provider == "claude"
        assert not sel.same_provider

    def test_only_one_provider_available_flags_same_provider_and_picks_alternate_model(self) -> None:
        """When only author's provider family is available, use alternate model and flag same-provider."""
        role_providers = ["claude"]
        sel = select_reviewer_provider(author_provider="claude", role_providers=role_providers)
        assert sel.provider == "claude"
        assert sel.same_provider
        assert sel.model is not None
        assert "haiku" in sel.model.lower() or sel.model != "default"

    def test_same_family_fallback_claude_selects_haiku_model(self) -> None:
        """Same-family fallback for 'claude' selects model 'haiku' with same_provider True."""
        role_providers = ["claude"]
        sel = select_reviewer_provider(author_provider="claude", role_providers=role_providers)
        assert sel.provider == "claude"
        assert sel.model == "haiku"
        assert sel.same_provider is True

    def test_fallback_provider_without_alternate_yields_none_model(self) -> None:
        """A fallback provider with no alternate (e.g. 'codex') yields model None and same_provider True."""
        role_providers = ["codex"]
        sel = select_reviewer_provider(author_provider="codex", role_providers=role_providers)
        assert sel.provider == "codex"
        assert sel.model is None
        assert sel.same_provider is True

    def test_alternate_models_has_no_stale_2024_or_default_alternate(self) -> None:
        """No value in ALTERNATE_MODELS contains '2024' and none equals 'default-alternate'."""
        assert ALTERNATE_MODELS == {"claude": "haiku", "antigravity": "gemini-3.8-flash-medium"}
        for model in ALTERNATE_MODELS.values():
            assert "2024" not in model
            assert model != "default-alternate"

    def test_detect_author_from_store(self) -> None:
        """Find author provider from run store by pr_number and repo."""
        mock_store = MagicMock()
        rec = make_run(id="run-1", repo="Runner_Dashboard", provider="codex", pr_number=42)
        mock_store.list_runs.return_value = [rec]

        author = detect_author_provider(repo="Runner_Dashboard", pr_number=42, store=mock_store)
        assert author == "codex"

    def test_detect_author_from_commit_trailers_fallback(self) -> None:
        """When not found in store, fall back to commit trailers (Agent-Id)."""
        mock_store = MagicMock()
        mock_store.list_runs.return_value = []
        mock_gh = MagicMock()
        mock_gh.get_commit_messages.return_value = ["feat: cool thing\n\nCo-authored-by: user\nAgent-Id: antigravity"]

        author = detect_author_provider(repo="Runner_Dashboard", pr_number=42, store=mock_store, gh_probe=mock_gh)
        assert author == "antigravity"


class TestVerdictParser:
    def test_valid_verdict_approve(self) -> None:
        line = "STAFF_RESULT: review approve #42"
        v = parse_review_verdict(line)
        assert v.status == "succeeded"
        assert v.verdict == "approve"
        assert v.pr_number == 42
        assert v.outcome == "review approve #42"

    def test_valid_verdict_changes(self) -> None:
        line = "Everything tested.\nSTAFF_RESULT: review changes #1803\n"
        v = parse_review_verdict(line)
        assert v.status == "succeeded"
        assert v.verdict == "changes"
        assert v.pr_number == 1803
        assert v.outcome == "review changes #1803"

    def test_valid_verdict_escalate(self) -> None:
        line = "STAFF_RESULT: review escalate #99"
        v = parse_review_verdict(line)
        assert v.status == "succeeded"
        assert v.verdict == "escalate"
        assert v.pr_number == 99
        assert v.outcome == "review escalate #99"

    def test_valid_verdict_same_provider_outcome(self) -> None:
        line = "STAFF_RESULT: review approve #42"
        v = parse_review_verdict(line, same_provider=True)
        assert v.status == "succeeded"
        assert v.same_provider
        assert v.outcome == "review approve #42 (same-provider)"

    def test_malformed_verdict_invalid_verdict_word(self) -> None:
        line = "STAFF_RESULT: review maybe #42"
        v = parse_review_verdict(line)
        assert v.status == "failed"
        assert v.verdict is None
        assert v.error is not None

    def test_malformed_verdict_missing_pr_number(self) -> None:
        line = "STAFF_RESULT: review approve"
        v = parse_review_verdict(line)
        assert v.status == "failed"

    def test_missing_verdict_line_becomes_needs_input(self) -> None:
        v_empty = parse_review_verdict("")
        assert v_empty.status == "needs_input"
        assert v_empty.outcome == ""

        v_no_result = parse_review_verdict("Reviewed the changes, everything looks clean.")
        assert v_no_result.status == "needs_input"
        assert v_no_result.outcome == ""

    def test_parse_outcome_helper(self) -> None:
        assert parse_outcome("STAFF_RESULT: review approve #42") == "review approve #42"
        expected_same = "review changes #10 (same-provider)"
        assert parse_outcome("STAFF_RESULT: review changes #10", same_provider=True) == expected_same
        assert parse_outcome("no verdict here") == ""


class TestReviewExecutor:
    def test_default_reviewer_is_code_reviewer(self) -> None:
        assert DEFAULT_REVIEWER_ROLE == "code-reviewer"

    def test_fallback_to_fleet_critic_when_code_reviewer_not_in_roster(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            prepared = prepare_review_params(
                {"repo": "Runner_Dashboard", "pr": 42},
                default_role="code-reviewer",
                roster={"fleet-critic": MagicMock()},
            )
        assert prepared["role"] == "fleet-critic"
        assert any("falling back to fleet-critic" in msg for msg in caplog.messages)

    def test_review_executor_never_passes_request_changes(self) -> None:
        """The review executor must never pass request_changes, blocking, or enforce parameters."""
        raw_params = {
            "repo": "Runner_Dashboard",
            "pr": 42,
            "request_changes": True,
            "event": "REQUEST_CHANGES",
            "blocking": True,
            "enforce": True,
        }
        prepared = prepare_review_params(
            raw_params,
            default_role="code-reviewer",
            roster={"code-reviewer": MagicMock()},
        )
        assert "request_changes" not in prepared
        assert "event" not in prepared
        assert "blocking" not in prepared
        assert "enforce" not in prepared
        assert prepared.get("role") == "code-reviewer"
        assert "Advisory only" in prepared.get("prompt", "") or DEFAULT_REVIEW_FOCUS in prepared.get("prompt", "")


class TestReviewScopeRepos:
    def test_review_scope_repos_returns_fallback_when_cache_empty(self) -> None:
        """review_scope_repos() returns FALLBACK_P0_P1_REPOS when the cache is empty."""
        cache_clear()
        assert review_scope_repos() == FALLBACK_P0_P1_REPOS

    def test_review_scope_repos_returns_cached_set_when_seeded(self) -> None:
        """review_scope_repos() returns cached P0/P1 repos when seeded."""
        cache_set(
            "projects:priorities",
            (
                {
                    "RepoAlpha": ProjectPriority(tier="P0"),
                    "RepoBeta": ProjectPriority(tier="P1"),
                    "RepoGamma": ProjectPriority(tier="P2"),
                },
                None,
            ),
        )
        assert review_scope_repos() == frozenset({"RepoAlpha", "RepoBeta"})

    def test_auto_review_uses_review_scope_repos(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """auto_review_if_eligible uses review_scope_repos to gate repo eligibility."""
        monkeypatch.setenv("STAFF_AUTO_REVIEW", "1")
        rec_unsupported = make_run(repo="UnscopedRepo", role="pragmatic-programmer")
        verdict = MagicMock(verification="verified", pr_number=42)
        assert not auto_review_if_eligible(rec_unsupported, verdict)

        cache_set(
            "projects:priorities",
            (
                {"UnscopedRepo": ProjectPriority(tier="P0")},
                None,
            ),
        )
        runner_mock = MagicMock()
        runner_mock.roles.return_value = {"code-reviewer": MagicMock()}
        store_mock = MagicMock()
        store_mock.list_runs.return_value = []
        runner_mock.store = store_mock
        res = auto_review_if_eligible(rec_unsupported, verdict, runner=runner_mock, store=store_mock)
        assert res is True
