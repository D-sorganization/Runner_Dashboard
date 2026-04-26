"""Tests for the cline provider upgrade in agent_remediation.PROVIDERS.

Pre-upgrade: cline was registered with dispatch_mode='future', editable=False,
experimental=True. Post-upgrade it should be a real selectable provider with
dispatch_mode='dashboard_local', editable=True, and a non-empty repair prompt.

These tests guard against accidental regression: someone reverting the
provider entry to 'future' would silently disable cline dispatch in the UI.
"""

from agent_remediation import (
    DEFAULT_PROVIDER_ORDER,
    PROVIDERS,
    FailureContext,
    provider_prompt,
)


def test_cline_provider_is_registered():
    assert "cline" in PROVIDERS
    p = PROVIDERS["cline"]
    assert p.label == "Cline"
    assert p.execution_mode == "local_plugin"


def test_cline_dispatch_mode_is_actionable():
    """Must NOT be 'future' — that disables it in the dispatch UI."""
    p = PROVIDERS["cline"]
    assert p.dispatch_mode != "future", (
        "cline dispatch_mode reverted to 'future'; the launcher integration "
        "needs dispatch_mode='dashboard_local'."
    )
    assert p.dispatch_mode == "dashboard_local"


def test_cline_provider_is_editable():
    """Editable=True is what lets the operator change the model + provider
    via the Cline Launcher tab editor."""
    assert PROVIDERS["cline"].editable is True


def test_cline_is_not_marked_experimental():
    """Experimental=True hides the provider from production dispatch lists."""
    assert PROVIDERS["cline"].experimental is False


def test_cline_in_default_provider_order():
    """Without this, the policy never offers cline as a dispatch target."""
    assert "cline" in DEFAULT_PROVIDER_ORDER


def test_provider_prompt_for_cline_is_non_empty_and_scope_locked():
    ctx = FailureContext(
        repository="D-sorganization/Tools",
        branch="fix/lint-error",
        workflow_name="CI Standard",
        run_id="123",
        failure_reason="ruff E501 line too long in foo.py:42",
        log_excerpt="E501 Line too long (95 > 88)",
    )
    prompt = provider_prompt("cline", ctx)
    assert "D-sorganization/Tools" in prompt
    assert "fix/lint-error" in prompt
    assert "ruff E501" in prompt
    # Scope-lock guards: the prompt must tell cline NOT to weaken tests
    # or suppress lint to make CI green (matches the address-prs skill).
    assert "weaken tests" in prompt or "weaken" in prompt
    # Cap on push attempts must be communicated to limit runner burn.
    assert "3" in prompt


def test_provider_prompt_for_cline_includes_yolo_context():
    """Cline runs with --yolo by default; the prompt should warn it that
    its actions are auto-approved so it doesn't expect prompts."""
    ctx = FailureContext(
        repository="x/y", branch="b", workflow_name="w",
        run_id="1", failure_reason="r", log_excerpt="l",
    )
    prompt = provider_prompt("cline", ctx)
    assert "auto-approve" in prompt.lower() or "yolo" in prompt.lower()
