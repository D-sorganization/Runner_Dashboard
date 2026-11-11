"""Unit tests for review-flow metrics aggregation (#4250, epic #4251).

Scope: queue depth, verdict mix (from already-parsed run outcomes), runs-by-machine/
provider, provider availability and the fail-fast provider-skip counter. Review
latency p50/p90 and reviewer disagreement rate are explicitly deferred to #4249
(the per-head-SHA verdict contract) and are not tested or implemented here.
"""

from __future__ import annotations

import pytest
from staff.review_metrics import (
    build_review_metrics_payload,
    is_green_checks,
    is_queue_candidate,
    provider_availability_rows,
    queue_depth,
    record_provider_skip,
    reset_provider_skip_counts,
    review_runs_by_machine_provider,
    select_available_reviewer,
    unhealthy_providers,
    verdict_mix,
)


def _pr(**kwargs: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "number": 1,
        "isDraft": False,
        "reviewDecision": "",
        "statusCheckRollup": [{"conclusion": "SUCCESS"}],
    }
    defaults.update(kwargs)
    return defaults


class TestIsGreenChecks:
    def test_no_checks_is_not_green(self) -> None:
        assert is_green_checks([]) is False
        assert is_green_checks(None) is False

    def test_all_success_is_green(self) -> None:
        assert is_green_checks([{"conclusion": "SUCCESS"}, {"conclusion": "NEUTRAL"}]) is True

    def test_one_failure_is_not_green(self) -> None:
        assert is_green_checks([{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]) is False


class TestQueueDepth:
    def test_green_non_draft_unapproved_counts(self) -> None:
        prs = [_pr(number=1), _pr(number=2)]
        assert queue_depth(prs) == 2

    def test_draft_pr_excluded(self) -> None:
        assert queue_depth([_pr(isDraft=True)]) == 0

    def test_approved_pr_excluded(self) -> None:
        assert queue_depth([_pr(reviewDecision="APPROVED")]) == 0

    def test_red_ci_pr_excluded(self) -> None:
        assert queue_depth([_pr(statusCheckRollup=[{"conclusion": "FAILURE"}])]) == 0

    def test_is_queue_candidate_matches_queue_depth(self) -> None:
        pr = _pr()
        assert is_queue_candidate(pr) is True
        assert queue_depth([pr]) == 1


class TestVerdictMix:
    def test_counts_approve_changes_escalate_from_code_reviewer_runs(self) -> None:
        runs = [
            {"role": "code-reviewer", "outcome": "review approve #1"},
            {"role": "code-reviewer", "outcome": "review changes #2"},
            {"role": "code-reviewer", "outcome": "review escalate #3 (same-provider)"},
            {"role": "code-reviewer", "outcome": "review approve #4"},
        ]
        assert verdict_mix(runs) == {"approve": 2, "changes": 1, "escalate": 1}

    def test_ignores_non_reviewer_runs_and_empty_outcomes(self) -> None:
        runs = [
            {"role": "worker", "outcome": "review approve #1"},
            {"role": "code-reviewer", "outcome": ""},
            {"role": "code-reviewer", "outcome": None},
        ]
        assert verdict_mix(runs) == {"approve": 0, "changes": 0, "escalate": 0}


class TestRunsByMachineProvider:
    def test_groups_code_reviewer_runs_by_machine_then_provider(self) -> None:
        runs = [
            {"role": "code-reviewer", "machine": "desk", "provider": "claude"},
            {"role": "code-reviewer", "machine": "desk", "provider": "claude"},
            {"role": "code-reviewer", "machine": "desk", "provider": "codex"},
            {"role": "code-reviewer", "machine": "laptop", "provider": "gemini"},
            {"role": "worker", "machine": "desk", "provider": "claude"},
        ]
        assert review_runs_by_machine_provider(runs) == {
            "desk": {"claude": 2, "codex": 1},
            "laptop": {"gemini": 1},
        }


class TestProviderAvailability:
    def test_unhealthy_providers_excludes_ok_status(self) -> None:
        snapshot = {
            "providers": {
                "claude": {"status": "expired"},
                "codex": {"status": "ok"},
                "antigravity": {"status": "stale"},
            }
        }
        assert unhealthy_providers(snapshot) == frozenset({"claude", "antigravity"})

    def test_provider_availability_rows_shape(self) -> None:
        snapshot = {"providers": {"claude": {"status": "ok", "detail": "fine"}}}
        rows = provider_availability_rows(snapshot)
        assert rows == [{"provider": "claude", "status": "ok", "detail": "fine"}]


class TestProviderSkipFailFast:
    def setup_method(self) -> None:
        reset_provider_skip_counts()

    def test_select_available_reviewer_skips_unhealthy_and_records(self) -> None:
        healthy, skipped = select_available_reviewer(["claude", "codex", "gemini"], frozenset({"claude"}))
        assert healthy == ["codex", "gemini"]
        assert skipped == ["claude"]

    def test_all_unhealthy_falls_back_to_full_candidate_list(self) -> None:
        healthy, skipped = select_available_reviewer(["claude", "codex"], frozenset({"claude", "codex"}))
        assert healthy == ["claude", "codex"]
        assert skipped == ["claude", "codex"]

    def test_record_provider_skip_accumulates(self) -> None:
        record_provider_skip("claude")
        record_provider_skip("claude")
        from staff.review_metrics import provider_skip_counts_snapshot

        assert provider_skip_counts_snapshot() == {"claude": 2}


@pytest.mark.unit
def test_build_review_metrics_payload_shape() -> None:
    reset_provider_skip_counts()
    payload = build_review_metrics_payload(
        queue_prs_by_repo={"Runner_Dashboard": [_pr()]},
        review_runs=[
            {"role": "code-reviewer", "outcome": "review approve #1", "machine": "desk", "provider": "claude"}
        ],
        provider_auth_snapshot={"providers": {"claude": {"status": "ok"}}},
        rate_limit_snapshot={"count_429": 1, "throttled_seconds": 66.0},
    )
    assert payload["queue_depth"] == 1
    assert payload["queue_depth_by_repo"] == {"Runner_Dashboard": 1}
    assert payload["verdict_mix"]["approve"] == 1
    assert payload["review_runs_by_machine_provider"] == {"desk": {"claude": 1}}
    assert payload["rate_limit"] == {"count_429": 1, "throttled_seconds": 66.0}
    assert "review_latency_p50_p90" in payload["deferred"]
    assert "reviewer_disagreement_rate" in payload["deferred"]
    assert "queue_depth" in payload["summary"]
    assert "throttled_time" in payload["summary"]
