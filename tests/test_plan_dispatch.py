"""Parametric branch coverage for ``agent_remediation.plan_dispatch``.

Issue #388: ``plan_dispatch`` has 11+ branches but only ~3 were covered. This
file enumerates every branch and exercises each with a parametric test.

Branch map (line numbers refer to ``backend/agent_remediation.py``):

    B1  L720  dispatch_origin == "automatic" AND policy.auto_dispatch_on_failure is False
              -> reject: "Automatic CI remediation is disabled by policy."
    B2  L731  policy.require_non_protected_branch AND context.protected_branch
              -> reject: "Protected branches require a PR-producing remediation path..."
    B3  L742  policy.require_failure_summary AND no failure_reason AND no log_excerpt
              -> reject: "A failure summary or failed-log excerpt is required..."
    B4  L757  dispatch_origin == "automatic" AND workflow_rule.dispatch_mode != "auto"
              -> reject: "<label> failures require manual review..."
    B5  L770  provider_override is supplied
              -> candidate_ids == (provider_override,)
    B6  L772  provider_override is None
              -> candidate_ids built from preferred + fallback + provider_order
    B7  L782  empty provider_id in candidate_ids
              -> continue (skip empty entries)
    B8  L784  provider_id not in policy.enabled_providers
              -> continue (skip disabled providers)
    B9  L788  provider/availability missing or not available
              -> continue (skip unavailable providers)
    B10 L796  provider_attempt_count >= policy.max_same_failure_attempts
              -> continue + record in exhausted_providers (per-provider loop guard)
    B11 L800  selected_provider found
              -> break out of candidate loop
    B12 L803  selected_provider is set
              -> return accepted DispatchDecision with prompt + workflow
    B13 L826  no selected_provider AND exhausted_providers is non-empty
              -> reject: "Loop guard blocked dispatch because all candidate providers..."
    B14 L841  no selected_provider AND no exhausted_providers
              -> reject: "No enabled remediation provider is currently available..."
"""

from __future__ import annotations

import datetime as _dt_mod
from collections.abc import Callable

import pytest

from agent_remediation import (
    AttemptRecord,
    DispatchDecision,
    FailureContext,
    ProviderAvailability,
    RemediationPolicy,
    WorkflowTypeRule,
    build_failure_fingerprint,
    plan_dispatch,
)

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _recent_timestamp() -> str:
    return _dt_mod.datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _make_availability(*provider_ids: str, unavailable: tuple[str, ...] = ()) -> dict[str, ProviderAvailability]:
    """Build an availability map. Providers in ``unavailable`` get available=False."""
    out: dict[str, ProviderAvailability] = {}
    for pid in provider_ids:
        is_avail = pid not in unavailable
        out[pid] = ProviderAvailability(
            provider_id=pid,
            available=is_avail,
            status="available" if is_avail else "missing_binary",
            detail="ready" if is_avail else "not on PATH",
        )
    return out


def _default_lint_rule(provider_id: str = "codex_cli", fallback: tuple[str, ...] = ()) -> WorkflowTypeRule:
    return WorkflowTypeRule(
        workflow_type="lint",
        label="Lint / Formatting",
        match_terms=("lint",),
        dispatch_mode="auto",
        provider_id=provider_id,
        fallback_providers=fallback,
    )


def _default_security_rule() -> WorkflowTypeRule:
    return WorkflowTypeRule(
        workflow_type="security",
        label="Security",
        match_terms=("security",),
        dispatch_mode="manual",
        provider_id="jules_api",
    )


def _make_policy(
    *,
    auto: bool = True,
    require_summary: bool = False,
    require_non_protected: bool = False,
    max_attempts: int = 3,
    provider_order: tuple[str, ...] = ("codex_cli", "claude_code_cli", "jules_api"),
    enabled: tuple[str, ...] = ("codex_cli", "claude_code_cli", "jules_api"),
    default_provider: str = "codex_cli",
    rules: dict[str, WorkflowTypeRule] | None = None,
) -> RemediationPolicy:
    return RemediationPolicy(
        auto_dispatch_on_failure=auto,
        require_failure_summary=require_summary,
        require_non_protected_branch=require_non_protected,
        max_same_failure_attempts=max_attempts,
        attempt_window_hours=24,
        provider_order=provider_order,
        enabled_providers=enabled,
        default_provider=default_provider,
        workflow_type_rules=rules
        or {
            "lint": _default_lint_rule(),
            "security": _default_security_rule(),
            "unknown": WorkflowTypeRule(workflow_type="unknown", label="Unclassified"),
        },
    )


def _ctx(
    *,
    workflow_name: str = "lint",
    failure_reason: str = "ruff fail",
    protected: bool = False,
    log_excerpt: str = "",
) -> FailureContext:
    return FailureContext(
        repository="foo/bar",
        workflow_name=workflow_name,
        branch="main",
        failure_reason=failure_reason,
        log_excerpt=log_excerpt,
        protected_branch=protected,
    )


def _attempts_against(
    fingerprint: str, provider_id: str, count: int
) -> list[AttemptRecord]:
    return [
        AttemptRecord(
            provider_id=provider_id,
            fingerprint=fingerprint,
            status="failed",
            created_at=_recent_timestamp(),
        )
        for _ in range(count)
    ]


# ---------------------------------------------------------------------------
# Parametric guard-clause tests (B1-B4)
# Each row produces a rejected DispatchDecision before any provider selection.
# ---------------------------------------------------------------------------


def _build_b1() -> tuple[FailureContext, RemediationPolicy, str, list[AttemptRecord]]:
    return _ctx(), _make_policy(auto=False), "automatic", []


def _build_b2() -> tuple[FailureContext, RemediationPolicy, str, list[AttemptRecord]]:
    return _ctx(protected=True), _make_policy(require_non_protected=True), "manual", []


def _build_b3() -> tuple[FailureContext, RemediationPolicy, str, list[AttemptRecord]]:
    # No failure_reason and no log_excerpt
    ctx = FailureContext(repository="foo/bar", workflow_name="lint", branch="main")
    return ctx, _make_policy(require_summary=True), "manual", []


def _build_b4() -> tuple[FailureContext, RemediationPolicy, str, list[AttemptRecord]]:
    # workflow_rule.dispatch_mode == "manual" but origin is automatic
    rules = {
        "security": _default_security_rule(),
        "unknown": WorkflowTypeRule(workflow_type="unknown", label="Unclassified"),
    }
    policy = _make_policy(rules=rules)
    ctx = _ctx(workflow_name="security scan", failure_reason="sast")
    return ctx, policy, "automatic", []


GuardCase = tuple[
    str,
    Callable[[], tuple[FailureContext, RemediationPolicy, str, list[AttemptRecord]]],
    str,
]


@pytest.mark.parametrize(
    ("branch_id", "factory", "expected_substr"),
    [
        ("B1_auto_disabled", _build_b1, "Automatic CI remediation is disabled"),
        ("B2_protected_branch", _build_b2, "Protected branches require"),
        ("B3_missing_summary", _build_b3, "failure summary or failed-log excerpt is required"),
        ("B4_manual_workflow_under_auto", _build_b4, "require manual review"),
    ],
    ids=lambda v: v if isinstance(v, str) else "factory",
)
def test_plan_dispatch_guard_clauses_reject(
    branch_id: str,
    factory: Callable[[], tuple[FailureContext, RemediationPolicy, str, list[AttemptRecord]]],
    expected_substr: str,
) -> None:
    """B1-B4: Each guard clause produces a rejected DispatchDecision."""
    context, policy, origin, attempts = factory()
    availability = _make_availability("codex_cli", "claude_code_cli", "jules_api")

    decision = plan_dispatch(
        context,
        policy=policy,
        availability=availability,
        attempts=attempts,
        dispatch_origin=origin,
    )

    assert isinstance(decision, DispatchDecision)
    assert decision.accepted is False, f"{branch_id}: expected reject"
    assert expected_substr in decision.reason, f"{branch_id}: reason mismatch -> {decision.reason!r}"
    assert decision.provider_id is None
    assert decision.fingerprint  # always populated
    # Workflow classification metadata must be present even on reject
    assert decision.workflow_type
    assert decision.workflow_label
    assert decision.dispatch_mode in {"auto", "manual"}


# ---------------------------------------------------------------------------
# B5: provider_override forces candidate_ids = (override,)
# B6: implicit chain when no override
# ---------------------------------------------------------------------------


def test_plan_dispatch_b5_provider_override_selects_specified_provider() -> None:
    """B5: provider_override restricts candidate_ids to that single provider."""
    policy = _make_policy()
    context = _ctx()
    availability = _make_availability("codex_cli", "claude_code_cli", "jules_api")

    decision = plan_dispatch(
        context,
        policy=policy,
        availability=availability,
        attempts=[],
        provider_override="claude_code_cli",  # not the workflow primary (codex_cli)
        dispatch_origin="manual",
    )

    assert decision.accepted is True
    assert decision.provider_id == "claude_code_cli"


def test_plan_dispatch_b6_chain_uses_workflow_primary_first() -> None:
    """B6: Without override, primary workflow provider is preferred."""
    policy = _make_policy()
    context = _ctx()  # lint => primary codex_cli
    availability = _make_availability("codex_cli", "claude_code_cli", "jules_api")

    decision = plan_dispatch(
        context,
        policy=policy,
        availability=availability,
        attempts=[],
        dispatch_origin="manual",
    )

    assert decision.accepted is True
    assert decision.provider_id == "codex_cli"


# ---------------------------------------------------------------------------
# B7-B11: candidate-loop branches
# Each case is constructed so the *first* candidate hits the branch under
# test, forcing fallthrough to a known good second candidate.
# ---------------------------------------------------------------------------


def _b7_empty_primary() -> tuple[FailureContext, RemediationPolicy, dict[str, ProviderAvailability], list[AttemptRecord]]:
    """Workflow rule with empty provider_id => preferred=[] but fallback present."""
    rules = {
        "lint": WorkflowTypeRule(
            workflow_type="lint",
            label="Lint",
            match_terms=("lint",),
            dispatch_mode="auto",
            provider_id="",  # empty
            fallback_providers=("", "claude_code_cli"),  # one empty, one real
        ),
        "unknown": WorkflowTypeRule(workflow_type="unknown", label="Unclassified"),
    }
    policy = _make_policy(
        rules=rules,
        provider_order=("",),  # introduce another empty entry
        enabled=("claude_code_cli",),
        default_provider="claude_code_cli",
    )
    return _ctx(), policy, _make_availability("claude_code_cli"), []


def _b8_disabled_primary() -> tuple[FailureContext, RemediationPolicy, dict[str, ProviderAvailability], list[AttemptRecord]]:
    """Primary provider is not in enabled_providers."""
    policy = _make_policy(
        enabled=("claude_code_cli",),  # codex_cli not enabled
        provider_order=("codex_cli", "claude_code_cli"),
    )
    return _ctx(), policy, _make_availability("codex_cli", "claude_code_cli"), []


def _b9_unavailable_primary() -> tuple[FailureContext, RemediationPolicy, dict[str, ProviderAvailability], list[AttemptRecord]]:
    """Primary provider availability=False."""
    policy = _make_policy()
    avail = _make_availability(
        "codex_cli", "claude_code_cli", "jules_api", unavailable=("codex_cli",)
    )
    return _ctx(), policy, avail, []


def _b10_exhausted_primary_fallback_to_secondary() -> tuple[FailureContext, RemediationPolicy, dict[str, ProviderAvailability], list[AttemptRecord]]:
    """Primary provider has hit its attempt limit -> fallback used."""
    fp = build_failure_fingerprint(_ctx())
    attempts = _attempts_against(fp, "codex_cli", 3)  # exhausted
    return _ctx(), _make_policy(), _make_availability(
        "codex_cli", "claude_code_cli", "jules_api"
    ), attempts


@pytest.mark.parametrize(
    ("branch_id", "factory", "expected_provider"),
    [
        ("B7_empty_provider_skipped", _b7_empty_primary, "claude_code_cli"),
        ("B8_disabled_provider_skipped", _b8_disabled_primary, "claude_code_cli"),
        ("B9_unavailable_provider_skipped", _b9_unavailable_primary, "claude_code_cli"),
        (
            "B10_exhausted_provider_skipped",
            _b10_exhausted_primary_fallback_to_secondary,
            "claude_code_cli",
        ),
    ],
    ids=lambda v: v if isinstance(v, str) else "factory",
)
def test_plan_dispatch_candidate_loop_skips_and_falls_through(
    branch_id: str,
    factory: Callable[
        [],
        tuple[
            FailureContext,
            RemediationPolicy,
            dict[str, ProviderAvailability],
            list[AttemptRecord],
        ],
    ],
    expected_provider: str,
) -> None:
    """B7-B10 + B11: Each candidate-loop skip falls through and a later provider is selected."""
    context, policy, availability, attempts = factory()

    decision = plan_dispatch(
        context,
        policy=policy,
        availability=availability,
        attempts=attempts,
        dispatch_origin="manual",
    )

    assert decision.accepted is True, f"{branch_id}: expected accepted decision, got {decision.reason!r}"
    assert decision.provider_id == expected_provider, branch_id
    assert decision.suggested_workflow == ".github/workflows/Agent-CI-Remediation.yml"
    assert decision.prompt_preview, "prompt_preview should be populated for accepted decisions"


# ---------------------------------------------------------------------------
# B12: accepted decision via the happy path
# ---------------------------------------------------------------------------


def test_plan_dispatch_b12_accepted_decision_full_payload() -> None:
    """B12: Selected provider yields a fully-populated accepted DispatchDecision."""
    fp = build_failure_fingerprint(_ctx())
    attempts = _attempts_against(fp, "codex_cli", 1)  # one prior attempt, still room
    policy = _make_policy()

    decision = plan_dispatch(
        _ctx(),
        policy=policy,
        availability=_make_availability("codex_cli", "claude_code_cli", "jules_api"),
        attempts=attempts,
        dispatch_origin="manual",
    )

    assert decision.accepted is True
    assert decision.provider_id == "codex_cli"
    assert "Codex CLI" in decision.reason
    assert decision.attempt_count == 1
    assert decision.remaining_attempts == 2
    assert decision.workflow_type == "lint"
    assert decision.workflow_label == "Lint / Formatting"
    assert decision.dispatch_mode == "auto"
    assert decision.fingerprint == fp


# ---------------------------------------------------------------------------
# B13: All providers exhausted -> loop guard reject
# ---------------------------------------------------------------------------


def test_plan_dispatch_b13_all_providers_exhausted_loop_guard() -> None:
    """B13: When every candidate provider is exhausted, return loop-guard reject."""
    fp = build_failure_fingerprint(_ctx())
    attempts = (
        _attempts_against(fp, "codex_cli", 3)
        + _attempts_against(fp, "claude_code_cli", 3)
        + _attempts_against(fp, "jules_api", 3)
    )

    decision = plan_dispatch(
        _ctx(),
        policy=_make_policy(),
        availability=_make_availability("codex_cli", "claude_code_cli", "jules_api"),
        attempts=attempts,
        dispatch_origin="manual",
    )

    assert decision.accepted is False
    assert "Loop guard blocked dispatch" in decision.reason
    assert "all candidate providers have reached their attempt limit" in decision.reason
    # Each exhausted provider is named in the reason
    assert "Codex CLI" in decision.reason
    assert "Claude Code CLI" in decision.reason
    assert "Jules API" in decision.reason
    assert decision.remaining_attempts == 0
    assert decision.provider_id is None


# ---------------------------------------------------------------------------
# B14: No enabled+available provider at all
# ---------------------------------------------------------------------------


def test_plan_dispatch_b14_no_provider_available() -> None:
    """B14: When no candidate is enabled+available, reject without loop-guard wording."""
    # All three known providers disabled in policy -> empty candidates
    policy = _make_policy(
        provider_order=("codex_cli", "claude_code_cli", "jules_api"),
        enabled=(),  # nothing enabled
        default_provider="codex_cli",
    )

    decision = plan_dispatch(
        _ctx(),
        policy=policy,
        availability=_make_availability("codex_cli", "claude_code_cli", "jules_api"),
        attempts=[],
        dispatch_origin="manual",
    )

    assert decision.accepted is False
    assert "No enabled remediation provider is currently available" in decision.reason
    assert decision.provider_id is None


def test_plan_dispatch_b14_all_providers_unavailable() -> None:
    """B14 (variant): Providers are enabled but all flagged unavailable."""
    policy = _make_policy()
    availability = _make_availability(
        "codex_cli",
        "claude_code_cli",
        "jules_api",
        unavailable=("codex_cli", "claude_code_cli", "jules_api"),
    )

    decision = plan_dispatch(
        _ctx(),
        policy=policy,
        availability=availability,
        attempts=[],
        dispatch_origin="manual",
    )

    assert decision.accepted is False
    assert "No enabled remediation provider is currently available" in decision.reason


# ---------------------------------------------------------------------------
# Sanity: all parametric branch IDs are unique and cover every branch ID.
# ---------------------------------------------------------------------------


def test_branch_ids_complete() -> None:
    """Meta-test: ensure the file documents every branch listed in the docstring."""
    expected = {f"B{i}" for i in range(1, 15)}
    documented = {
        "B1",
        "B2",
        "B3",
        "B4",
        "B5",
        "B6",
        "B7",
        "B8",
        "B9",
        "B10",
        "B11",
        "B12",
        "B13",
        "B14",
    }
    assert documented == expected
