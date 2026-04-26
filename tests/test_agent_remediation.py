"""Unit tests for agent_remediation.py — failure context, policy, and workflow classification."""

from agent_remediation import (
    AttemptRecord,
    DispatchDecision,
    FailureContext,
    ProviderAvailability,
    RemediationPolicy,
    WorkflowTypeRule,
    _attempts_for_provider,
    build_failure_fingerprint,
    classify_workflow_type,
    load_policy,
    plan_dispatch,
)

# ---------------------------------------------------------------------------
# Helper: classify_failure wraps classify_workflow_type with default policy
# ---------------------------------------------------------------------------


def classify_failure(context: FailureContext, policy: RemediationPolicy | None = None) -> WorkflowTypeRule:
    """Thin wrapper so tests can call classify_failure(ctx) as described in the issue."""
    if policy is None:
        policy = load_policy()
    return classify_workflow_type(context, policy)


# ---------------------------------------------------------------------------
# FailureContext.from_dict — valid construction
# ---------------------------------------------------------------------------


def test_failure_context_from_dict_valid() -> None:
    data = {"repository": "Foo", "workflow_name": "CI", "branch": "main"}
    ctx = FailureContext.from_dict(data)
    assert ctx.repository == "Foo"
    assert ctx.workflow_name == "CI"
    assert ctx.branch == "main"


# ---------------------------------------------------------------------------
# classify_failure — "CI Standard" workflow_name → "test" type
# ---------------------------------------------------------------------------


def test_classify_failure_ci_standard_returns_test_type() -> None:
    ctx = FailureContext(repository="Foo", workflow_name="CI Standard", branch="main")
    rule = classify_failure(ctx)
    assert isinstance(rule, WorkflowTypeRule)
    assert rule.workflow_type == "test"


# ---------------------------------------------------------------------------
# classify_failure — "ruff lint check" workflow_name → "lint" type
# ---------------------------------------------------------------------------


def test_classify_failure_ruff_lint_returns_lint_type() -> None:
    ctx = FailureContext(repository="Foo", workflow_name="ruff lint check", branch="main")
    rule = classify_failure(ctx)
    assert isinstance(rule, WorkflowTypeRule)
    assert rule.workflow_type == "lint"


# ---------------------------------------------------------------------------
# RemediationPolicy — default policy loads without error and has sane defaults
# ---------------------------------------------------------------------------


def test_load_policy_returns_remediation_policy() -> None:
    policy = load_policy()
    assert isinstance(policy, RemediationPolicy)
    assert policy.max_same_failure_attempts > 0
    assert "unknown" in policy.workflow_type_rules


# ---------------------------------------------------------------------------
# FailureContext — protected_branch flag is preserved
# ---------------------------------------------------------------------------


def test_failure_context_protected_branch_flag() -> None:
    ctx = FailureContext.from_dict(
        {"repository": "Foo", "workflow_name": "CI", "branch": "main", "protected_branch": True}
    )
    assert ctx.protected_branch is True


# ---------------------------------------------------------------------------
# Fallback provider chain tests
# ---------------------------------------------------------------------------


def _make_availability(*provider_ids: str) -> dict[str, ProviderAvailability]:
    """Helper to create availability map with all requested providers available."""
    return {
        pid: ProviderAvailability(provider_id=pid, available=True, status="available", detail="ready")
        for pid in provider_ids
    }


def test_fallback_provider_chain_uses_fallback_when_primary_exhausted() -> None:
    """When primary provider is exhausted, fallback to next in chain."""
    fingerprint = build_failure_fingerprint(
        FailureContext(repository="foo/bar", workflow_name="CI", branch="main", failure_reason="lint")
    )
    # Primary codex_cli has 3 attempts (at limit), fallback claude_code_cli has 0
    attempts = [
        AttemptRecord(
            provider_id="codex_cli",
            fingerprint=fingerprint,
            status="failed",
            created_at="2026-04-25T12:00:00Z",
        )
        for _ in range(3)
    ] + [
        AttemptRecord(
            provider_id="claude_code_cli",
            fingerprint=fingerprint,
            status="failed",
            created_at="2026-04-25T12:00:00Z",
        )
        for _ in range(2)
    ]

    policy = RemediationPolicy(
        auto_dispatch_on_failure=True,
        require_failure_summary=False,
        require_non_protected_branch=False,
        max_same_failure_attempts=3,
        attempt_window_hours=24,
        provider_order=("codex_cli", "claude_code_cli", "jules_api"),
        enabled_providers=("codex_cli", "claude_code_cli", "jules_api"),
        default_provider="codex_cli",  # primary is codex_cli
    )
    context = FailureContext(repository="foo/bar", workflow_name="CI", branch="main", failure_reason="lint")
    availability = _make_availability("codex_cli", "claude_code_cli", "jules_api")

    decision = plan_dispatch(
        context, policy=policy, availability=availability, attempts=attempts, dispatch_origin="manual"
    )

    # codex_cli is exhausted (3 attempts), claude_code_cli has 2 attempts (under limit)
    assert decision.accepted is True
    assert decision.provider_id == "claude_code_cli"
    assert decision.attempt_count == 2
    assert decision.remaining_attempts == 1


def test_fallback_provider_chain_exhausted_all_providers() -> None:
    """When all providers in chain are exhausted, reject dispatch."""
    fingerprint = build_failure_fingerprint(
        FailureContext(repository="foo/bar", workflow_name="CI", branch="main", failure_reason="lint")
    )
    # All providers exhausted: codex_cli (3), claude_code_cli (3), jules_api (3)
    attempts = (
        [
            AttemptRecord(
                provider_id="codex_cli",
                fingerprint=fingerprint,
                status="failed",
                created_at="2026-04-25T12:00:00Z",
            )
            for _ in range(3)
        ]
        + [
            AttemptRecord(
                provider_id="claude_code_cli",
                fingerprint=fingerprint,
                status="failed",
                created_at="2026-04-25T12:00:00Z",
            )
            for _ in range(3)
        ]
        + [
            AttemptRecord(
                provider_id="jules_api",
                fingerprint=fingerprint,
                status="failed",
                created_at="2026-04-25T12:00:00Z",
            )
            for _ in range(3)
        ]
    )

    policy = RemediationPolicy(
        auto_dispatch_on_failure=True,
        require_failure_summary=False,
        require_non_protected_branch=False,
        max_same_failure_attempts=3,
        attempt_window_hours=24,
        provider_order=("codex_cli", "claude_code_cli", "jules_api"),
        enabled_providers=("codex_cli", "claude_code_cli", "jules_api"),
        default_provider="codex_cli",
    )
    context = FailureContext(repository="foo/bar", workflow_name="CI", branch="main", failure_reason="lint")
    availability = _make_availability("codex_cli", "claude_code_cli", "jules_api")

    decision = plan_dispatch(
        context, policy=policy, availability=availability, attempts=attempts, dispatch_origin="manual"
    )

    assert decision.accepted is False
    assert "all candidate providers have reached their attempt limit" in decision.reason


def test_fallback_provider_chain_uses_fallback_providers_field() -> None:
    """WorkflowTypeRule.fallback_providers defines explicit fallback order."""
    fingerprint = build_failure_fingerprint(
        FailureContext(repository="foo/bar", workflow_name="security scan", branch="main", failure_reason="sast")
    )
    # jules_api (primary) exhausted, fallback claude_code_cli available
    attempts = [
        AttemptRecord(
            provider_id="jules_api",
            fingerprint=fingerprint,
            status="failed",
            created_at="2026-04-25T12:00:00Z",
        )
        for _ in range(3)
    ]

    policy = RemediationPolicy(
        auto_dispatch_on_failure=True,
        require_failure_summary=False,
        require_non_protected_branch=False,
        max_same_failure_attempts=3,
        attempt_window_hours=24,
        provider_order=("jules_api", "codex_cli", "claude_code_cli"),
        enabled_providers=("jules_api", "codex_cli", "claude_code_cli"),
        default_provider="jules_api",
        workflow_type_rules={
            "security": WorkflowTypeRule(
                workflow_type="security",
                label="Security",
                match_terms=("security",),
                dispatch_mode="manual",
                provider_id="jules_api",
                fallback_providers=("claude_code_cli", "codex_cli"),
            ),
            "unknown": WorkflowTypeRule(workflow_type="unknown", label="Unclassified"),
        },
    )
    context = FailureContext(repository="foo/bar", workflow_name="security scan", branch="main", failure_reason="sast")
    availability = _make_availability("jules_api", "claude_code_cli", "codex_cli")

    decision = plan_dispatch(
        context, policy=policy, availability=availability, attempts=attempts, dispatch_origin="manual"
    )

    # Should skip exhausted jules_api and pick claude_code_cli (first fallback)
    assert decision.accepted is True
    assert decision.provider_id == "claude_code_cli"
    assert decision.attempt_count == 0
    assert decision.remaining_attempts == 3


def test_attempts_for_provider_filters_by_provider_id() -> None:
    """_attempts_for_provider only counts attempts for the specific provider."""
    fingerprint = "test|fingerprint"
    attempts = [
        AttemptRecord(provider_id="a", fingerprint=fingerprint, status="failed", created_at="2026-04-25T12:00:00Z"),
        AttemptRecord(provider_id="b", fingerprint=fingerprint, status="failed", created_at="2026-04-25T12:00:00Z"),
        AttemptRecord(provider_id="a", fingerprint=fingerprint, status="failed", created_at="2026-04-25T12:00:00Z"),
    ]

    result = _attempts_for_provider(fingerprint, "a", attempts, window_hours=24)
    assert len(result) == 2

    result = _attempts_for_provider(fingerprint, "b", attempts, window_hours=24)
    assert len(result) == 1

    result = _attempts_for_provider(fingerprint, "c", attempts, window_hours=24)
    assert len(result) == 0


def test_per_provider_attempt_count_separate_from_global() -> None:
    """Each provider gets its own attempt budget."""
    fingerprint = build_failure_fingerprint(
        FailureContext(repository="foo/bar", workflow_name="CI", branch="main", failure_reason="lint")
    )
    # codex_cli exhausted but claude_code_cli fresh
    attempts = [
        AttemptRecord(
            provider_id="codex_cli",
            fingerprint=fingerprint,
            status="failed",
            created_at="2026-04-25T12:00:00Z",
        )
        for _ in range(3)
    ]

    policy = RemediationPolicy(
        auto_dispatch_on_failure=True,
        require_failure_summary=False,
        require_non_protected_branch=False,
        max_same_failure_attempts=3,
        attempt_window_hours=24,
        provider_order=("codex_cli", "claude_code_cli"),
        enabled_providers=("codex_cli", "claude_code_cli"),
        default_provider="codex_cli",
    )
    context = FailureContext(repository="foo/bar", workflow_name="CI", branch="main", failure_reason="lint")
    availability = _make_availability("codex_cli", "claude_code_cli")

    decision = plan_dispatch(
        context, policy=policy, availability=availability, attempts=attempts, dispatch_origin="manual"
    )

    # Global count is 3, but per-provider codex_cli is exhausted, so fallback to claude_code_cli
    assert decision.accepted is True
    assert decision.provider_id == "claude_code_cli"
    assert decision.attempt_count == 0  # fresh provider
    assert decision.remaining_attempts == 3
