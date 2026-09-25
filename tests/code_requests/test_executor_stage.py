"""Unit and simulation tests for Code Request executor stage (CR-5, #1287).

Covers:
- Dependency wave partitioning and acyclic execution order
- Pinned profile routing and tier escalation when child demands higher tier
- Conductor routing resolving cheapest capable provider
- Coordination: claims, leases, do-not-automate skip, roster priority
- PR metadata validation (Fixes #N and agent:<agent>)
- Retry and tier escalation (2 failures at ollama -> cli -> strong)
- Strong tier exhaustion -> needs-human-triage and dependent branch pausing
- 3-wave end-to-end plan simulation reaching done
- Concurrency cap per repository per wave
- Cost accumulation and rollup visibility
"""

from __future__ import annotations

import pytest
from code_requests.executor_coordination import (
    can_dispatch_child,
    is_higher_priority,
    post_child_lease,
    release_child_lease,
    roster_rank,
    validate_pr_submission,
)
from code_requests.executor_models import (
    ChildExecutionRecord,
    ChildExecutionState,
    ChildIssuePayload,
    ExecutionConfig,
    ExecutorTier,
)
from code_requests.executor_router import (
    escalate_tier,
    normalize_tier,
    resolve_child_executor,
)
from code_requests.executor_stage import ExecutorPipeline, compute_waves
from code_requests.model import CodeRequest, Requester, RequesterKind


def _make_dummy_request(pinned_profile: str | None = None) -> CodeRequest:
    return CodeRequest(
        id="cr-test-1",
        repository="Runner_Dashboard",
        requester=Requester(id="tester", kind=RequesterKind.HUMAN),
        created_at="2026-09-25T12:00:00Z",
        updated_at="2026-09-25T12:00:00Z",
        executor_profile_id=pinned_profile,
    )


class TestExecutorRouter:
    """Tests for tier escalation, profile pinning, and Conductor provider resolution."""

    def test_normalize_tier(self):
        assert normalize_tier("tier:ollama") == ExecutorTier.OLLAMA
        assert normalize_tier("CLI") == ExecutorTier.CLI
        assert normalize_tier("strong") == ExecutorTier.STRONG
        assert normalize_tier(ExecutorTier.OLLAMA) == ExecutorTier.OLLAMA

    def test_tier_escalation_chain(self):
        assert escalate_tier(ExecutorTier.OLLAMA) == ExecutorTier.CLI
        assert escalate_tier(ExecutorTier.CLI) == ExecutorTier.STRONG
        assert escalate_tier(ExecutorTier.STRONG) is None

    def test_pinned_profile_matching_or_higher(self):
        req = _make_dummy_request(pinned_profile="claude_code_cli")
        child = ChildExecutionRecord(
            key="c1",
            repository="Runner_Dashboard",
            title="lint fix",
            tier=ExecutorTier.OLLAMA,
            task_class="lint",
        )
        provider, tier, reason = resolve_child_executor(child, request=req)
        assert provider == "claude_code_cli"
        assert "pinned_profile" in reason

    def test_pinned_profile_escalation_when_child_demands_higher_tier(self):
        req = _make_dummy_request(pinned_profile="ollama")
        child = ChildExecutionRecord(
            key="c1",
            repository="Runner_Dashboard",
            title="refactor core architecture",
            tier=ExecutorTier.STRONG,
            task_class="refactor",
        )
        provider, tier, reason = resolve_child_executor(child, request=req)
        assert tier == ExecutorTier.CLI
        assert "escalation: child tier 'strong' demands higher tier" in reason

    def test_conductor_resolves_cheapest_capable_in_budget(self):
        child = ChildExecutionRecord(
            key="c1",
            repository="Runner_Dashboard",
            title="format check",
            tier=ExecutorTier.OLLAMA,
            task_class="format",
        )
        provider, tier, reason = resolve_child_executor(child, remaining_budget_usd=0.0)
        assert provider in ("ollama", "local")
        assert tier == ExecutorTier.OLLAMA
        assert "conductor_routing" in reason


class TestExecutorCoordination:
    """Tests for multi-agent coordination, claims, leases, and PR metadata."""

    def test_roster_priority_ordering(self):
        assert roster_rank("user") < roster_rank("claude")
        assert roster_rank("claude") < roster_rank("codex")
        assert roster_rank("codex") < roster_rank("local")
        assert is_higher_priority("claude", "local")
        assert not is_higher_priority("local", "claude")

    def test_skip_do_not_automate(self):
        child = ChildExecutionRecord(key="c1", repository="Runner_Dashboard", title="manual fix", issue_number=10)
        can_run, reason = can_dispatch_child(child, "local", labels=["do-not-automate"])
        assert not can_run
        assert "do-not-automate" in reason

    def test_skip_when_held_by_higher_priority_agent(self):
        child = ChildExecutionRecord(key="c1", repository="Runner_Dashboard", title="task", issue_number=10)

        def fake_check(_repo: str, _issue: int) -> dict[str, object]:
            return {"available": True, "held": True, "agent": "claude"}

        can_run, reason = can_dispatch_child(child, "local", claim_check_fn=fake_check)
        assert not can_run
        assert "held_by_higher_priority" in reason

    def test_allow_continuation_by_same_agent(self):
        child = ChildExecutionRecord(key="c1", repository="Runner_Dashboard", title="task", issue_number=10)

        def fake_check(_repo: str, _issue: int) -> dict[str, object]:
            return {"available": True, "held": True, "agent": "local"}

        can_run, reason = can_dispatch_child(child, "local", claim_check_fn=fake_check)
        assert can_run
        assert "continuation" in reason

    def test_lease_posting_and_release(self):
        child = ChildExecutionRecord(key="c1", repository="Runner_Dashboard", title="task", issue_number=10)
        posted_records = []
        released_records = []

        def fake_post(repo: str, issue: int, **kw: object) -> dict[str, object]:
            posted_records.append((repo, issue, kw))
            return {"lease": {"receipt": "rcpt-1"}}

        def fake_release(repo: str, issue: int, **kw: object) -> dict[str, object]:
            released_records.append((repo, issue, kw))
            return {"ok": True}

        ok, receipt = post_child_lease(child, "local", "sess-1", lease_poster_fn=fake_post)
        assert ok
        assert receipt == "rcpt-1"
        assert len(posted_records) == 1

        rel_ok, rel_msg = release_child_lease(child, "local", "sess-1", "done", lease_releaser_fn=fake_release)
        assert rel_ok
        assert rel_msg == "released"
        assert len(released_records) == 1

    def test_pr_metadata_validation(self):
        valid_body = "Summary\n\nFixes #42\n"
        valid_labels = ["agent:local"]
        ok, _ = validate_pr_submission(valid_body, valid_labels, 42, "local")
        assert ok

        bad_body = "Summary with no reference"
        ok2, err2 = validate_pr_submission(bad_body, valid_labels, 42, "local")
        assert not ok2
        assert "missing_reference" in err2

        bad_labels = ["some-label"]
        ok3, err3 = validate_pr_submission(valid_body, bad_labels, 42, "local")
        assert not ok3
        assert "missing_label" in err3


class TestExecutorPipeline:
    """Tests for pipeline orchestrator, wave computation, and failure escalation."""

    def test_compute_waves_acyclic(self):
        children = [
            ChildIssuePayload(key="A", title="A", repository="repo", dependencies=[]),
            ChildIssuePayload(key="B", title="B", repository="repo", dependencies=["A"]),
            ChildIssuePayload(key="C", title="C", repository="repo", dependencies=["A"]),
            ChildIssuePayload(key="D", title="D", repository="repo", dependencies=["B", "C"]),
        ]
        waves = compute_waves(children)
        assert waves == [["A"], ["B", "C"], ["D"]]

    def test_compute_waves_cycle_raises(self):
        children = [
            ChildIssuePayload(key="A", title="A", repository="repo", dependencies=["B"]),
            ChildIssuePayload(key="B", title="B", repository="repo", dependencies=["A"]),
        ]
        with pytest.raises(ValueError, match="dependency_cycle"):
            compute_waves(children)

    def test_concurrency_cap_per_repo(self):
        config = ExecutionConfig(max_concurrent_per_repo=2)
        pipeline = ExecutorPipeline("cr-test", config=config)
        children = [ChildIssuePayload(key=f"task_{i}", title=f"Task {i}", repository="repo1") for i in range(4)]
        pipeline.initialize(children)
        ready = pipeline.get_ready_children()
        assert len(ready) == 2  # capped at 2

    def test_tier_escalation_after_two_failures(self):
        pipeline = ExecutorPipeline("cr-test")
        payload = [ChildIssuePayload(key="task_1", title="Task", repository="repo", tier=ExecutorTier.OLLAMA)]
        pipeline.initialize(payload)

        # Attempt 1: fail at OLLAMA
        pipeline.report_failed("task_1", "syntax error", cost=0.0)
        assert pipeline.children["task_1"].tier == ExecutorTier.OLLAMA
        assert pipeline.children["task_1"].attempts == 1

        # Attempt 2: fail again at OLLAMA -> escalates to CLI
        pipeline.report_failed("task_1", "syntax error again", cost=0.0)
        assert pipeline.children["task_1"].tier == ExecutorTier.CLI
        assert pipeline.children["task_1"].attempts == 0
        assert "escalated from ollama to cli" in pipeline.children["task_1"].escalation_history

    def test_strong_tier_exhaustion_pauses_branch_for_human(self):
        pipeline = ExecutorPipeline("cr-test")
        payload = [
            ChildIssuePayload(key="root", title="Root", repository="repo", tier=ExecutorTier.STRONG),
            ChildIssuePayload(key="dep", title="Dep", repository="repo", dependencies=["root"]),
        ]
        pipeline.initialize(payload)

        # Attempt 1 at STRONG
        pipeline.report_failed("root", "test fail 1", cost=0.10)
        assert pipeline.children["root"].state == ChildExecutionState.QUEUED

        # Attempt 2 at STRONG -> exhaustion
        pipeline.report_failed("root", "test fail 2", cost=0.10)
        assert pipeline.children["root"].state == ChildExecutionState.PAUSED_FOR_HUMAN
        assert "root" in pipeline.paused_branches
        # Downstream dep should be BLOCKED
        assert pipeline.children["dep"].state == ChildExecutionState.BLOCKED
        assert "dep" in pipeline.paused_branches

        rollup = pipeline.get_rollup()
        assert rollup.state == "failed"
        assert rollup.total_cost == pytest.approx(0.20)
        assert "root" in rollup.failed_children
        assert "dep" in rollup.blocked_children

    def test_three_wave_plan_simulation_reaching_done(self):
        """Acceptance Criteria: Simulation of a 3-wave plan with Conductor routing,

        claims, tier escalation, and successful rollup to done.
        """
        pipeline = ExecutorPipeline("cr-plan-1")
        plan = [
            # Wave 0
            ChildIssuePayload(
                key="W0_A",
                title="Core Schemas",
                repository="Runner_Dashboard",
                tier=ExecutorTier.OLLAMA,
                task_class="format",
            ),
            ChildIssuePayload(
                key="W0_B",
                title="Config Parser",
                repository="Runner_Dashboard",
                tier=ExecutorTier.CLI,
                task_class="bug",
            ),
            # Wave 1 (depends on W0_A and W0_B)
            ChildIssuePayload(
                key="W1_A",
                title="Service Layer",
                repository="Runner_Dashboard",
                dependencies=["W0_A", "W0_B"],
                tier=ExecutorTier.CLI,
                task_class="feature",
            ),
            # Wave 2 (depends on W1_A)
            ChildIssuePayload(
                key="W2_A",
                title="UI View",
                repository="Runner_Dashboard",
                dependencies=["W1_A"],
                tier=ExecutorTier.CLI,
                task_class="feature",
            ),
        ]
        pipeline.initialize(plan)
        assert len(pipeline.waves) == 3

        # Wave 0 execution
        ready_w0 = pipeline.get_ready_children()
        assert {c.key for c in ready_w0} == {"W0_A", "W0_B"}

        # Dispatch W0_A, fails once, then succeeds
        ok_a, _ = pipeline.dispatch_child("W0_A")
        assert ok_a
        pipeline.report_failed("W0_A", "linter crash", cost=0.01)
        assert pipeline.children["W0_A"].state == ChildExecutionState.QUEUED  # retries at OLLAMA

        pipeline.dispatch_child("W0_A")
        pipeline.report_pr_opened("W0_A", 101)
        pipeline.report_ci_status("W0_A", "running")
        pipeline.report_merged("W0_A", cost=0.01)
        assert pipeline.children["W0_A"].state == ChildExecutionState.MERGED

        # Dispatch W0_B
        pipeline.dispatch_child("W0_B")
        pipeline.report_pr_opened("W0_B", 102)
        pipeline.report_merged("W0_B", cost=0.05)
        assert pipeline.children["W0_B"].state == ChildExecutionState.MERGED

        # Pipeline should have advanced to Wave 1
        assert pipeline.current_wave_idx == 1

        # Wave 1 execution
        ready_w1 = pipeline.get_ready_children()
        assert [c.key for c in ready_w1] == ["W1_A"]
        pipeline.dispatch_child("W1_A")
        pipeline.report_pr_opened("W1_A", 103)
        pipeline.report_merged("W1_A", cost=0.10)

        # Pipeline advances to Wave 2
        assert pipeline.current_wave_idx == 2
        ready_w2 = pipeline.get_ready_children()
        assert [c.key for c in ready_w2] == ["W2_A"]
        pipeline.dispatch_child("W2_A")
        pipeline.report_pr_opened("W2_A", 104)
        pipeline.report_merged("W2_A", cost=0.15)

        # Check final rollup
        rollup = pipeline.get_rollup()
        assert rollup.state == "done"
        assert len(rollup.completed_children) == 4
        assert rollup.total_cost == pytest.approx(0.32)
        assert not rollup.failed_children
        assert not rollup.blocked_children
