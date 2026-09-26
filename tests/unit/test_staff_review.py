"""Unit tests for code-reviewer runtime (WP-1.3, Issue #1518)."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from staff.action_executors import DEFAULT_REVIEWER_ROLE
from staff.review import (
    DEFAULT_REVIEW_FOCUS,
    PROVIDER_FAMILY,
    auto_review_if_eligible,
    detect_author_provider,
    parse_outcome,
    parse_review_verdict,
    prepare_review_params,
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


class TestAutoReviewTrigger:
    def test_auto_review_triggered_when_verified_on_p0_p1_repo(self) -> None:
        rec = make_run(
            id="run-1",
            role="pragmatic-programmer",
            repo="Runner_Dashboard",
            status="succeeded",
            branch="feat/test",
            pr_number=42,
        )
        verdict = MagicMock()
        verdict.verification = "verified"
        verdict.pr_number = 42

        dispatched: list[dict[str, Any]] = []

        def fake_dispatch(params: dict[str, Any]) -> None:
            dispatched.append(params)

        with patch.dict("os.environ", {"STAFF_AUTO_REVIEW": "1"}):
            result = auto_review_if_eligible(rec, verdict, dispatch_fn=fake_dispatch)
            assert result is True
            assert len(dispatched) == 1
            assert dispatched[0]["repo"] == "Runner_Dashboard"
            assert dispatched[0]["pr"] == 42

    def test_auto_review_not_triggered_when_setting_is_off(self) -> None:
        rec = make_run(
            id="run-1",
            role="pragmatic-programmer",
            repo="Runner_Dashboard",
            status="succeeded",
            branch="feat/test",
            pr_number=42,
        )
        verdict = MagicMock()
        verdict.verification = "verified"
        verdict.pr_number = 42

        dispatched: list[dict[str, Any]] = []
        with patch.dict("os.environ", {"STAFF_AUTO_REVIEW": "0"}):
            result = auto_review_if_eligible(rec, verdict, dispatch_fn=lambda p: dispatched.append(p))
            assert result is False
            assert len(dispatched) == 0

    def test_auto_review_not_triggered_on_unranked_repo(self) -> None:
        rec = make_run(
            id="run-1",
            role="pragmatic-programmer",
            repo="Random_Unranked_Repo",
            status="succeeded",
            branch="feat/test",
            pr_number=42,
        )
        verdict = MagicMock()
        verdict.verification = "verified"
        verdict.pr_number = 42

        dispatched: list[dict[str, Any]] = []
        with patch.dict("os.environ", {"STAFF_AUTO_REVIEW": "1"}):
            result = auto_review_if_eligible(rec, verdict, dispatch_fn=lambda p: dispatched.append(p))
            assert result is False
            assert len(dispatched) == 0

    def test_auto_review_not_triggered_on_reviewer_itself(self) -> None:
        rec = make_run(
            id="run-1",
            role="code-reviewer",
            repo="Runner_Dashboard",
            status="succeeded",
            branch="feat/test",
            pr_number=42,
        )
        verdict = MagicMock()
        verdict.verification = "verified"
        verdict.pr_number = 42

        dispatched: list[dict[str, Any]] = []
        with patch.dict("os.environ", {"STAFF_AUTO_REVIEW": "1"}):
            result = auto_review_if_eligible(rec, verdict, dispatch_fn=lambda p: dispatched.append(p))
            assert result is False
            assert len(dispatched) == 0


class TestRuntimeWiring:
    """Runtime wiring the mocked tests above missed (#1579)."""

    def test_detect_author_against_a_real_run_store(self, tmp_path: Any) -> None:
        from staff.store import RunStore

        store = RunStore(tmp_path / "runs.sqlite3")
        try:
            store.create_run(make_run(id="run-a", repo="Tools", provider="gemini", pr_number=42))
            store.create_run(make_run(id="run-b", repo="Runner_Dashboard", provider="codex", pr_number=42))
            author = detect_author_provider(repo="Runner_Dashboard", pr_number=42, store=store)
        finally:
            store.close()
        assert author == "codex"

    def test_selection_uses_roster_providers_and_trailer_probe(self) -> None:
        role = MagicMock()
        role.providers = ("gemini", "antigravity", "claude")
        probe = MagicMock()
        probe.get_commit_messages.return_value = ["feat: x\n\nAgent-Id: gemini"]
        prepared = prepare_review_params(
            {"repo": "Runner_Dashboard", "pr": 42},
            roster={"code-reviewer": role},
            gh_probe=probe,
        )
        assert prepared["provider"] == "claude"

    def test_same_provider_review_is_tagged_and_reaches_the_outcome(self) -> None:
        from staff.review import SAME_PROVIDER_TAG, review_outcome

        role = MagicMock()
        role.providers = ("claude",)
        prepared = prepare_review_params(
            {"repo": "Runner_Dashboard", "pr": 42, "provider": None},
            roster={"code-reviewer": role},
            store=MagicMock(list_runs=MagicMock(return_value=[make_run(provider="claude", pr_number=42)])),
        )
        assert SAME_PROVIDER_TAG in prepared["prompt"]
        outcome = review_outcome(prepared["prompt"], "STAFF_RESULT: review approve #42")
        assert outcome == "review approve #42 (same-provider)"
        assert review_outcome("plain prompt", "STAFF_RESULT: review approve #42") == "review approve #42"

    def test_auto_review_submits_through_the_runner_and_dedupes(self, tmp_path: Any) -> None:
        from staff.store import RunStore

        rec = make_run(id="run-1", role="pragmatic-programmer", repo="Runner_Dashboard", pr_number=42)
        verdict = MagicMock(verification="verified", pr_number=42)
        runner = MagicMock()
        store = RunStore(tmp_path / "runs.sqlite3")
        try:
            with patch.dict("os.environ", {"STAFF_AUTO_REVIEW": "1"}):
                assert auto_review_if_eligible(rec, verdict, store=store, runner=runner) is True
                req = runner.submit.call_args.args[0]
                assert (req.role, req.repo, req.pr) == ("code-reviewer", "Runner_Dashboard", 42)

                store.create_run(
                    make_run(id="run-rev", role="code-reviewer", repo="Runner_Dashboard", target_ref="PR #42")
                )
                runner.reset_mock()
                assert auto_review_if_eligible(rec, verdict, store=store, runner=runner) is False
                runner.submit.assert_not_called()
        finally:
            store.close()

    def test_auto_review_reports_a_failed_submit(self) -> None:
        rec = make_run(id="run-1", repo="Runner_Dashboard", pr_number=42)
        verdict = MagicMock(verification="verified", pr_number=42)
        runner = MagicMock()
        runner.submit.side_effect = ValueError("provider unavailable")
        store = MagicMock(list_runs=MagicMock(return_value=[]))
        with patch.dict("os.environ", {"STAFF_AUTO_REVIEW": "1"}):
            assert auto_review_if_eligible(rec, verdict, store=store, runner=runner) is False

    def test_non_numeric_pr_is_an_invalid_param_not_a_crash(self) -> None:
        from staff.action_executors import execute_review_pr

        res = execute_review_pr({"repo": "Runner_Dashboard", "pr": "abc"}, MagicMock())
        assert res.success is False
        assert res.failure_class == "invalid_params"
