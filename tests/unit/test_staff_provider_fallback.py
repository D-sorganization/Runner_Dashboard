"""Declarative provider fallback on capacity / rate limits (#2021)."""

from __future__ import annotations

import pytest
from staff.classifier import classify_run_failure
from staff.adapters import ADAPTERS
from staff.model_tiers import allowed_models_for_provider
from staff.provider_fallback import (
    IMMEDIATE_FALLBACK_CLASSES,
    ProviderFallbackRule,
    ProviderFallbackTarget,
    match_provider_fallback,
    parse_provider_fallback_rules,
    resolve_fallback_target_model,
    validate_provider_fallback_rules,
    validate_provider_fallback_rules_data,
)
from staff.retry import is_retryable_class, should_retry
from staff.roles import RoleSpec
from staff.validator import validate_role_data


def _pr_remediator_role_data() -> dict:
    return {
        "name": "pr-remediator",
        "title": "PR Remediator",
        "summary": "x",
        "playbook": "docs/staff/pr-remediator.md",
        "prompt_template": None,
        "instructions": "fix prs",
        "providers": ["codex", "antigravity"],
        "model": "gpt-6-luna",
        "schedule": "0 22 * * *",
        "window": {"start": "21:00", "end": "06:00"},
        "repos": ["Runner_Dashboard"],
        "scope": {},
        "budget": {"usd_per_run": 0, "usd_per_day": 0},
        "permissions": {
            "lease": True,
            "push_branch": True,
            "open_pr": True,
            "merge": False,
            "host_shell": False,
            "notify_user": False,
        },
        "reports_to": "user",
        "holds": [],
        "surface": "dashboard",
        "retry": {
            "provider_fallbacks": [
                {
                    "when": ["provider_capacity", "rate_limited"],
                    "from": "codex",
                    "to": {"provider": "antigravity", "model": "gemini-3.8-flash-high"},
                }
            ]
        },
    }


@pytest.mark.unit
def test_classify_codex_model_at_capacity() -> None:
    diag = classify_run_failure(
        "codex",
        1,
        output_text="Error: Selected model is at capacity. Try again later.",
    )
    assert diag.failure_class == "provider_capacity"
    assert diag.retryable is True


@pytest.mark.unit
def test_unknown_failure_does_not_match_capacity() -> None:
    diag = classify_run_failure("codex", 1, output_text="STAFF_RESULT: failed tests in test_foo.py")
    assert diag.failure_class == "unknown"
    assert diag.failure_class not in IMMEDIATE_FALLBACK_CLASSES


@pytest.mark.unit
def test_parse_and_validate_pr_remediator_fallback_rules() -> None:
    data = _pr_remediator_role_data()
    rules = parse_provider_fallback_rules(data)
    assert len(rules) == 1
    assert rules[0].target.provider == "antigravity"
    assert rules[0].target.model == "gemini-3.8-flash-high"
    assert validate_provider_fallback_rules_data(data) == []
    assert validate_role_data(data) == []


@pytest.mark.unit
def test_schema_rejects_unknown_when_class() -> None:
    data = _pr_remediator_role_data()
    data["retry"]["provider_fallbacks"][0]["when"] = ["unknown"]
    problems = validate_provider_fallback_rules_data(data)
    assert any("unsupported class" in p for p in problems)


@pytest.mark.unit
def test_schema_rejects_fallback_target_not_in_providers() -> None:
    data = _pr_remediator_role_data()
    data["retry"]["provider_fallbacks"][0]["to"]["provider"] = "claude"
    problems = validate_provider_fallback_rules_data(data)
    assert any("not in role providers" in p for p in problems)


@pytest.mark.unit
def test_match_immediate_on_capacity() -> None:
    rules = (
        ProviderFallbackRule(
            when=("provider_capacity", "rate_limited"),
            from_provider="codex",
            target=ProviderFallbackTarget("antigravity", "gemini-3.8-flash-high"),
        ),
    )
    hit = match_provider_fallback(rules, "provider_capacity", "codex", retries_exhausted=False)
    assert hit is not None
    assert hit.target.provider == "antigravity"


@pytest.mark.unit
def test_rate_limited_waits_until_retries_exhausted() -> None:
    rules = (
        ProviderFallbackRule(
            when=("rate_limited",),
            from_provider="codex",
            target=ProviderFallbackTarget("antigravity", None),
        ),
    )
    assert match_provider_fallback(rules, "rate_limited", "codex", retries_exhausted=False) is None
    assert match_provider_fallback(rules, "rate_limited", "codex", retries_exhausted=True) is not None


@pytest.mark.unit
def test_no_fallback_on_real_task_failure_class() -> None:
    rules = parse_provider_fallback_rules(_pr_remediator_role_data())
    assert match_provider_fallback(rules, "unknown", "codex", retries_exhausted=True) is None
    assert match_provider_fallback(rules, "no_result", "codex", retries_exhausted=True) is None


@pytest.mark.unit
def test_fallback_default_target_model_resolves_for_target_provider() -> None:
    """Codex gpt-6-luna must not carry to antigravity when rule omits an explicit model (#2499)."""
    role = RoleSpec(
        name="pr-remediator",
        title="PR Remediator",
        providers=("codex", "antigravity"),
        model="gpt-6-luna",
    )
    resolved = resolve_fallback_target_model(
        target=ProviderFallbackTarget("antigravity", None),
        role=role,
        source_provider="codex",
        target_provider="antigravity",
        adapter=ADAPTERS["antigravity"],
    )
    assert resolved is not None
    assert resolved != "gpt-6-luna"
    assert resolved in allowed_models_for_provider("antigravity", ADAPTERS["antigravity"])


@pytest.mark.unit
def test_provider_capacity_skips_same_provider_retry() -> None:
    role = RoleSpec(name="r", title="R", providers=("codex",))
    from staff.store import RunRecord

    rec = RunRecord(
        id="run-1",
        role="r",
        provider="codex",
        model="gpt-6-luna",
        machine="test",
        repo="",
        target_kind="",
        target_ref="",
        prompt="x",
        failure_class="provider_capacity",
        attempt=1,
        max_attempts=3,
    )
    ok, reason = should_retry(rec, role)
    assert ok is False
    assert "declarative fallback" in reason
    assert is_retryable_class("provider_capacity") is True
