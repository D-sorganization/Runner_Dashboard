"""Safety rules for the opt-in hub review/arm coordinator (Refs #4249)."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from staff.review_arm.policy import Candidate, decide
from staff.review_arm.service import ReviewArmService

HEAD = "a" * 40


def candidate(**kwargs):
    return replace(
        Candidate(
            "Runner_Dashboard",
            42,
            HEAD,
            author_provider="cursor-agent",
            checks_green=True,
            checks_head=HEAD,
            holds_available=True,
        ),
        **kwargs,
    )


@pytest.mark.parametrize(
    "label",
    [
        "c3",
        "HOLD",
        "on-hold",
        "on hold",
        "sidekick packaging",
        "sidekick-packaging",
        "assessment board",
        "assessment-board",
        "do-not-merge",
        "do-not-automate",
        "blocked",
    ],
)
def test_hold_labels(label):
    assert decide(candidate(labels=(label,))).reason == "hold_label"


@pytest.mark.parametrize(
    "repo,pr",
    [
        ("Gasification_Model", 5873),
        ("Tools_Private", 2230),
        ("Repository_Management", 2796),
        ("Repository_Management", 2797),
        ("Runner_Dashboard", 4241),
        ("UpstreamDrift", 14798),
        ("AffineDrift", 5194),
        ("Gasification_Model", 5824),
        ("Gasification_Model", 5735),
        ("Gasification_Model", 5898),
        ("Gasification_Model", 5899),
        ("UpstreamDrift", 15172),
        ("UpstreamDrift", 10333),
    ],
)
def test_seeded_holds(repo, pr):
    assert decide(candidate(repo=repo, pr=pr)).reason == "held_pr"


def test_blocked_head_cannot_be_approved_again():
    assert decide(candidate(blocked=True, approved=True)).reason == "blocked_head"


def test_moved_head_cannot_arm():
    assert decide(candidate(approved=True, checks_head="b" * 40)).reason == "checks_not_at_head"


def test_round_cap():
    assert decide(candidate(prior_runs=4)).action == "escalate"
    assert decide(candidate(prior_runs=3)).action == "review"


def test_armed_skip():
    assert decide(candidate(armed=True)).reason == "already_armed"


def test_hold_source_error_fails_closed():
    assert decide(candidate(holds_available=False)).reason == "holds_unavailable"


@pytest.mark.parametrize("author_provider", ["", "Codex", "gpt", "codxe", "unknown"])
def test_unknown_author_escalates(author_provider):
    decision = decide(candidate(author_provider=author_provider))
    assert decision.action == "escalate"
    assert decision.reason == "unknown_author"
    assert decision.provider == ""


def test_provider_is_independent_and_only_codex_or_claude():
    assert decide(candidate(author_provider="codex")).provider == "claude"
    assert decide(candidate(author_provider="claude")).provider == "codex"
    assert decide(candidate(author_provider="cursor-agent")).provider in {"codex", "claude"}


def test_exact_head_approval_arms_only_when_checks_green():
    assert decide(candidate(approved=True)).action == "arm"
    assert decide(candidate(approved=True, checks_green=False)).action == "skip"


def test_queued_head_skipped_and_new_head_can_be_reviewed():
    assert decide(candidate(queued=True)).reason == "already_reviewed_or_queued"
    assert decide(candidate()).action == "review"


def test_flag_off_does_no_io():
    port = Mock()
    service = ReviewArmService(port, enabled=False)
    service.cycle()
    assert not port.mock_calls


def test_dry_run_logs_decisions_without_dispatch_or_arm():
    port = Mock()
    port.candidates.return_value = [candidate(), candidate(pr=43, approved=True)]
    service = ReviewArmService(port, enabled=True, dry_run=True)
    service.cycle()
    port.dispatch.assert_not_called()
    port.arm.assert_not_called()
    assert service.status()["counts"] == {"review": 1, "arm": 1}


def test_live_arm_revalidates_before_mutation():
    port = Mock()
    port.candidates.return_value = [candidate(approved=True)]
    port.refresh.return_value = candidate(head_sha="b" * 40, approved=False)
    service = ReviewArmService(port, enabled=True, dry_run=False)
    service.cycle()
    port.arm.assert_not_called()
    assert service.status()["counts"] == {"head_moved": 1}


def test_missing_check_head_is_not_evidence():
    assert decide(candidate(checks_head="")).action == "skip"


def test_blocking_verdict_survives_restart_and_later_approve(tmp_path):
    from staff.review_arm.ledger import Ledger, Verdict

    path = tmp_path / "ledger.sqlite"
    ledger = Ledger(path)
    ledger.ingest(
        Verdict(
            "Runner_Dashboard",
            42,
            HEAD,
            "changes",
            "claude",
            "reviewer-1",
            "sonnet",
            blocking=1,
            observations=("backend/a.py:3",),
            tests=("pytest",),
        )
    )
    ledger.ingest(
        Verdict(
            "Runner_Dashboard",
            42,
            HEAD,
            "approve",
            "claude",
            "reviewer-1",
            "sonnet",
            observations=("backend/a.py:3",),
            tests=("pytest",),
        )
    )
    assert Ledger(path).state("Runner_Dashboard", 42, HEAD) == (True, False)
    assert ledger.state("Runner_Dashboard", 42, "b" * 40) == (False, False)


def test_zero_blocking_changes_counts_as_approve_with_evidence(tmp_path):
    from staff.review_arm.ledger import Ledger, Verdict

    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.ingest(
        Verdict(
            "Runner_Dashboard",
            42,
            HEAD,
            "changes",
            "claude",
            "reviewer-1",
            "sonnet",
            observations=("backend/a.py:3",),
            tests=("pytest",),
        )
    )
    assert ledger.state("Runner_Dashboard", 42, HEAD) == (False, True)


def test_rubber_stamp_is_escalated(tmp_path):
    from staff.review_arm.ledger import Ledger, Verdict

    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.ingest(Verdict("Runner_Dashboard", 42, HEAD, "approve", "claude", "reviewer-1", "sonnet"))
    assert ledger.state("Runner_Dashboard", 42, HEAD) == (False, False)


def test_override_requires_operator_identity_and_reason(tmp_path):
    from staff.review_arm.ledger import Ledger

    ledger = Ledger(tmp_path / "ledger.sqlite")
    with pytest.raises(ValueError):
        ledger.override("Runner_Dashboard", 42, HEAD, operator="", reason="")


def test_intake_lists_once_per_repo_and_only_probes_eligible_candidates():
    from staff.review_arm.intake import Intake

    source = Mock()
    source.list_open.return_value = [candidate(armed=True), candidate(pr=43)]
    source.confirm.return_value = candidate(pr=43)
    intake = Intake(source, repos=("Runner_Dashboard",))
    assert list(intake.candidates()) == [candidate(armed=True), candidate(pr=43)]
    source.list_open.assert_called_once_with("Runner_Dashboard")
    source.confirm.assert_called_once_with(candidate(pr=43))


def test_intake_rejects_head_move_during_required_checks():
    from staff.review_arm.intake import Intake

    source = Mock()
    source.list_open.return_value = [candidate()]
    source.confirm.return_value = candidate(head_sha="b" * 40, checks_head="b" * 40)
    result = list(Intake(source, repos=("Runner_Dashboard",)).candidates())
    assert len(result) == 1
    assert decide(result[0]).action == "skip"


def test_intake_confirmation_error_is_fail_closed():
    from staff.review_arm.intake import Intake

    source = Mock()
    source.list_open.return_value = [candidate()]
    source.confirm.side_effect = OSError("holds unavailable")
    result = list(Intake(source, repos=("Runner_Dashboard",)).candidates())
    assert decide(result[0]).reason == "holds_unavailable"


def test_rate_limit_stops_repo_scan_without_more_calls():
    from staff.review_arm.intake import Backoff, Intake

    source = Mock()
    source.list_open.side_effect = Backoff("retry later")
    with pytest.raises(Backoff):
        list(Intake(source, repos=("Runner_Dashboard", "Tools")).candidates())
    source.list_open.assert_called_once()


def test_settings_off_by_default_and_only_configured_hub():
    from staff.review_arm.settings import Settings

    env = {"RD_REVIEW_ARM_ENABLED": "1", "RD_REVIEW_ARM_HOST": "hub-a"}
    assert not Settings.from_env({}, machine="hub-a").enabled
    assert not Settings.from_env({"RD_REVIEW_ARM_ENABLED": "1"}, machine="hub-a").enabled
    assert not Settings.from_env({**env, "RD_REVIEW_ARM_HOST": " "}, machine="hub-a").enabled
    assert not Settings.from_env(env, machine="node-b").enabled
    assert Settings.from_env(env, machine="HUB-A").enabled
    assert Settings.from_env({}, machine="hub-a").interval_seconds == 600


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, True), ("", True), ("ture", True), ("1", True), ("false", False), (" NO ", False), ("0", False)],
)
def test_settings_dry_run_only_disabled_by_explicit_opt_out(raw, expected):
    from staff.review_arm.settings import Settings

    env = (
        {"RD_REVIEW_ARM_ENABLED": "1"} if raw is None else {"RD_REVIEW_ARM_ENABLED": "1", "RD_REVIEW_ARM_DRY_RUN": raw}
    )
    assert Settings.from_env(env, machine="hub-a").dry_run is expected


def test_settings_extend_holds_without_removing_seeds():
    from staff.review_arm.settings import Settings

    settings = Settings.from_env({"RD_REVIEW_ARM_HELD_PRS": "Tools#999"}, machine="hub-a")
    assert ("Tools", 999) in settings.held_prs
    assert ("Gasification_Model", 5873) in settings.held_prs


def test_approve_with_blocking_finding_is_invalid():
    from staff.review_arm.ledger import Verdict

    with pytest.raises(ValueError):
        Verdict("Runner_Dashboard", 42, HEAD, "approve", "claude", "reviewer-1", "sonnet", blocking=1)


def test_evidence_requires_a_diff_anchor_or_named_rule():
    from staff.review_arm.ledger import Verdict

    with pytest.raises(ValueError):
        Verdict(
            "Runner_Dashboard",
            42,
            HEAD,
            "approve",
            "claude",
            "reviewer-1",
            "sonnet",
            observations=("looks good",),
            tests=("pytest",),
        )


def test_override_clears_block_but_does_not_approve(tmp_path):
    from staff.review_arm.ledger import Ledger, Verdict

    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.ingest(Verdict("Runner_Dashboard", 42, HEAD, "escalate", "codex", "reviewer-2", "gpt", blocking=1))
    ledger.override("Runner_Dashboard", 42, HEAD, operator="Dieter", reason="false positive")
    assert ledger.state("Runner_Dashboard", 42, HEAD) == (False, False)


def test_rate_limit_during_action_stops_cycle():
    from staff.review_arm.intake import Backoff

    port = Mock()
    port.candidates.return_value = [candidate(), candidate(pr=43)]
    port.refresh.side_effect = lambda c: c
    port.dispatch.side_effect = Backoff("rate limit")
    service = ReviewArmService(port, enabled=True, dry_run=False)
    service.cycle()
    port.dispatch.assert_called_once()


def test_github_source_uses_existing_snapshot_and_rejects_partial_labels():
    from staff.review_arm.github_source import GitHubSource

    query = Mock(
        return_value={
            "repository": {
                "pullRequests": {
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [
                        {
                            "number": 42,
                            "headRefOid": HEAD,
                            "mergeable": "MERGEABLE",
                            "mergeStateStatus": "CLEAN",
                            "labels": {"pageInfo": {"hasNextPage": True}, "nodes": [{"name": "hold"}]},
                        }
                    ],
                }
            }
        }
    )
    source = GitHubSource(query=query, context=lambda c: c, confirm=lambda c: c)
    rows = list(source.list_open("Runner_Dashboard"))
    assert not rows[0].holds_available
    assert rows[0].labels == ("hold",)
    query.assert_called_once()


def test_intake_does_not_treat_optional_rollup_failure_as_required_failure():
    from staff.review_arm.intake import Intake

    source = Mock()
    source.list_open.return_value = [candidate(checks_green=False)]
    source.confirm.return_value = candidate()
    assert list(Intake(source, repos=("Runner_Dashboard",)).candidates()) == [candidate()]
    source.confirm.assert_called_once()


def test_list_query_requests_completeness_evidence():
    """The str.replace patches on the shepherd query must apply, or every cycle fails closed silently."""
    from staff.review_arm.github_source import LIST_QUERY

    assert "labels(first: 100) { pageInfo { hasNextPage }" in LIST_QUERY
    assert "pullRequests(states: OPEN, first: 100) {\n      pageInfo { hasNextPage }\n      nodes" in LIST_QUERY
