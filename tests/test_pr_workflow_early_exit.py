"""Tests for pull_request workflow early-exit guard (issue #2764)."""

from __future__ import annotations

import pytest

from shared_scripts.pr_workflow_early_exit import evaluate_pull_request_guard


def test_non_pull_request_never_skips() -> None:
    decision = evaluate_pull_request_guard(event_name="merge_group", pr_state="open", event_head_sha="abc", live_head_sha="abc")
    assert decision.skip is False
    assert decision.reason == ""


def test_closed_pr_skips() -> None:
    decision = evaluate_pull_request_guard(event_name="pull_request", pr_state="closed", event_head_sha="abc", live_head_sha="abc")
    assert decision.skip is True
    assert "closed" in decision.reason


def test_stale_head_sha_skips() -> None:
    decision = evaluate_pull_request_guard(event_name="pull_request", pr_state="open", event_head_sha="old", live_head_sha="new")
    assert decision.skip is True
    assert "head" in decision.reason.lower()


def test_open_pr_at_current_head_runs() -> None:
    decision = evaluate_pull_request_guard(event_name="pull_request", pr_state="open", event_head_sha="same", live_head_sha="same")
    assert decision.skip is False


def test_pr_state_is_case_insensitive() -> None:
    decision = evaluate_pull_request_guard(event_name="pull_request", pr_state="OPEN", event_head_sha="x", live_head_sha="x")
    assert decision.skip is False
